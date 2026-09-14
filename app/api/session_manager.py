"""
API session management.

Deliberately has ZERO dependency on FastAPI -- this holds the actual
session-isolation logic (create/lookup/expire a session's dataset +
orchestrator), so it's fully unit-testable in this project's dev sandbox
even though `fastapi` itself isn't installable here (see
app/api/main.py's NOTE ON TESTING). main.py is a thin HTTP wrapper around
this class; every behavioral decision below is exercised directly by
tests/test_session_manager.py.

WHY THIS EXISTS (round 3 fix): the previous version of app/api/main.py held
ONE global `_db`/`_orchestrator` for the whole process -- every API client
shared the same loaded dataset. Concretely: client A uploads sales.csv,
client B uploads a different file a moment later, and client A's next
`/query` call would silently run against client B's data. This class fixes
that by giving each session (identified by a server-generated UUID
returned from `create_session()`) its own `AnalyticalDatabase.create_session_database()`
instance -- a distinct temp file, per app/data/database.py's design (see
that module's docstring for why file-backed + thread-safe, not `:memory:`,
is required here).

SESSION EXPIRY: a simple TTL is used (`SESSION_TTL_SECONDS`, default 2
hours) -- session state expires and is pruned on subsequent calls to
`prune_expired()`, called on every `create_session()`/`get_session()`
rather than via a background task/scheduler. This is the "choose the
simplest reliable approach" call for a project at this scale: an
in-process TTL check adds no infrastructure (no scheduler, no external
store), at the cost of not proactively cleaning up temp files between
requests -- for high-traffic or long-running deployments, a proper
background sweep would be worth adding (see README "Known limitations").
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from app.agents.orchestrator import Orchestrator
from app.core.config import Settings
from app.data.database import AnalyticalDatabase
from app.data.profiler import DataProfile
from app.llm.base import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_SESSION_TTL_SECONDS = 2 * 60 * 60  # 2 hours


class SessionNotFoundError(KeyError):
    pass


@dataclass
class SessionState:
    session_id: str
    db: AnalyticalDatabase
    orchestrator: Orchestrator
    profile: DataProfile | None = None
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_accessed_at = time.time()


class SessionManager:
    """Thread-safe registry of active API sessions. One instance is created
    per FastAPI process (see app/api/main.py)."""

    def __init__(self, llm_client: LLMClient, settings: Settings, ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS):
        self._llm = llm_client
        self._settings = settings
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def create_session(self) -> SessionState:
        self.prune_expired()
        session_id = str(uuid.uuid4())
        db = AnalyticalDatabase.create_session_database(backend=self._settings.database_backend)
        orchestrator = Orchestrator(db, self._llm, max_result_rows=self._settings.max_result_rows, settings=self._settings)
        state = SessionState(session_id=session_id, db=db, orchestrator=orchestrator)
        with self._lock:
            self._sessions[session_id] = state
        logger.info("Created API session %s (db=%s)", session_id, db.path)
        return state

    def get_session(self, session_id: str) -> SessionState:
        self.prune_expired()
        with self._lock:
            state = self._sessions.get(session_id)
        if state is None:
            raise SessionNotFoundError(
                f"Session '{session_id}' was not found -- it may have expired (sessions expire after "
                f"{self._ttl_seconds // 60} minutes of inactivity) or never existed. Upload a file again "
                f"to start a new session."
            )
        state.touch()
        return state

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            state = self._sessions.pop(session_id, None)
        if state is None:
            return False
        state.db.close()
        logger.info("Deleted API session %s", session_id)
        return True

    def prune_expired(self) -> int:
        """Remove sessions idle longer than the TTL, closing each one's
        database (which deletes its temp file -- see AnalyticalDatabase.close())
        so expired sessions don't leak temp files on disk."""
        now = time.time()
        with self._lock:
            expired_ids = [sid for sid, s in self._sessions.items() if now - s.last_accessed_at > self._ttl_seconds]
            expired_states = [self._sessions.pop(sid) for sid in expired_ids]
        for state in expired_states:
            state.db.close()
            logger.info("Pruned expired API session %s", state.session_id)
        return len(expired_states)

    def active_session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def close_all(self) -> None:
        """Close every session's database and remove its temp file --
        intended for graceful shutdown/test teardown."""
        with self._lock:
            states = list(self._sessions.values())
            self._sessions.clear()
        for state in states:
            state.db.close()
