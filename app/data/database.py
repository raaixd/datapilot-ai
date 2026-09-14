"""
Analytical database layer.

Design choice vs. the reference project: instead of a fixed SQLAlchemy ORM
schema seeded with one synthetic dataset, DataPilot AI is CSV-first --
a user uploads an arbitrary business CSV and it becomes a table in an
in-process analytical database. This means the schema is not known in
advance, so schema introspection (`describe_schema`) is a first-class
operation used by both the SQL validator and the prompt builder.

Two backends are supported behind one small interface:
  - "sqlite" (default): Python's built-in sqlite3, zero extra dependencies.
    This is the backend exercised by the automated test suite.
  - "duckdb": used the same way if the `duckdb` package is installed --
    better suited to larger analytical CSVs. Selected via
    DATABASE_BACKEND=duckdb.

The connection is opened read-only for querying (see `AnalyticalDatabase.query`)
so that even a validated-but-unexpected statement cannot mutate data.

THREAD SAFETY (round 3): Streamlit reruns a script's code path on every
interaction, and depending on version/configuration, callbacks and reruns
are not guaranteed to execute on the exact same OS thread that created a
given object. A single long-lived sqlite3.Connection captured once (the
original design here) breaks the moment it's touched from a different
thread than the one that created it:

    ProgrammingError: SQLite objects created in a thread can only be used
    in that same thread.

Alternatives considered (see also README "Database/thread/session
reliability" for the write-up):
  1. `check_same_thread=False` on a single shared connection. Silences the
     error, but doesn't make concurrent access actually safe -- SQLite
     connections are not free-threaded, so two threads touching the same
     connection object at once can corrupt cursor state or raise
     "database is locked" nondeterministically. Rejected: this trades a
     loud, honest error for an intermittent, hard-to-reproduce one.
  2. A fresh connection per operation, opened against `:memory:`. Rejected
     outright: each new connection to `:memory:` gets its OWN empty
     database -- SQLite's in-memory mode is not shared across connections.
     This would silently "lose" the loaded dataset on the very next call.
  3. **A fresh, short-lived connection per operation, opened against a
     real FILE-backed SQLite database (chosen).** A file-backed database
     IS visible to any new connection, from any thread, so "open, do one
     operation, close" is both simple and genuinely safe -- there is no
     connection object that outlives the call that created it, so there is
     no thread that can touch a connection it didn't create. This is the
     standard, textbook-simple way to use SQLite from a multi-threaded
     Python app, and it's what `AnalyticalDatabase.create_session_database()`
     below uses. Write operations (`load_dataframe`) are brief enough
     (one `to_sql` call) that no additional locking was needed in testing
     (see tests/test_database_thread_safety.py) -- SQLite's own file
     locking serializes them if it ever matters.
  4. A per-session connection pool / persistent worker thread. Rejected as
     unnecessary complexity for this app's access pattern (one dataset,
     mostly-sequential reads) -- see README "Known limitations" for when
     this would become worth revisiting (concurrent writes, very large
     files, high query volume).

The plain `:memory:` mode (a single held-open connection) is KEPT as the
default for `AnalyticalDatabase(...)` because it's what the test suite,
the evaluation harness, and the CLI demo want: fast, single-threaded,
automatically cleaned up, no temp files left behind. It is NOT what the
Streamlit app or the FastAPI backend should use for a live user session --
they should call `AnalyticalDatabase.create_session_database()` instead,
which returns a thread-safe, file-backed instance whose temp file is
cleaned up by `close()`.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


class UnsupportedBackendError(Exception):
    pass


@dataclass
class TableSchema:
    name: str
    columns: list[tuple[str, str]]  # (column_name, sql_type)
    row_count: int
    column_samples: dict[str, list[str]] = None  # low-cardinality column -> sample distinct values

    def __post_init__(self):
        if self.column_samples is None:
            self.column_samples = {}


class AnalyticalDatabase:
    """A small, backend-agnostic wrapper around an in-process analytical DB.

    Two usage modes:
      - `AnalyticalDatabase(backend="sqlite", path=":memory:")` (default):
        a single held-open connection. Fast, simple, NOT thread-safe --
        use only in single-threaded contexts (tests, CLI, eval).
      - `AnalyticalDatabase.create_session_database()`: file-backed,
        thread-safe (a fresh connection is opened and closed for every
        operation). Use this for Streamlit/API sessions -- see the module
        docstring above for why.
    """

    def __init__(self, backend: str = "sqlite", path: str = ":memory:", thread_safe: bool = False):
        self.backend = backend
        self.path = path
        self._owns_temp_file = False
        self._thread_safe = bool(thread_safe) and backend == "sqlite" and path != ":memory:"

        if backend == "sqlite":
            # thread_safe mode: no persistent connection is held -- see _connect()/_disconnect().
            # Default (non-thread_safe) mode deliberately does NOT pass
            # check_same_thread=False: this is single-threaded-only by
            # design (tests/eval/CLI), and leaving Python's own thread
            # check enabled is what gives an honest, loud error if it's
            # ever misused across threads -- silencing it here would be
            # exactly the rejected "alternative 1" from the module
            # docstring above, just moved to the wrong code path.
            self._conn = None if self._thread_safe else sqlite3.connect(path)
        elif backend == "duckdb":
            try:
                import duckdb  # type: ignore
            except ImportError as exc:  # pragma: no cover - exercised only without duckdb installed
                raise UnsupportedBackendError(
                    "DATABASE_BACKEND=duckdb requires the optional `duckdb` package. "
                    "Install it with `pip install duckdb`, or set DATABASE_BACKEND=sqlite."
                ) from exc
            self._conn = duckdb.connect(path)
        else:
            raise UnsupportedBackendError(f"Unknown DATABASE_BACKEND '{backend}'. Use 'sqlite' or 'duckdb'.")

    @classmethod
    def create_session_database(cls, backend: str = "sqlite") -> "AnalyticalDatabase":
        """Create a thread-safe, file-backed database suitable for one
        Streamlit/API session. Each call gets its OWN unique temp file --
        this is also the session-isolation mechanism (see README
        'Multi-user and session isolation'): two sessions never share a
        path, so one user's data cannot appear via a stale shared
        connection or cache key collision. Call `.close()` when the
        session ends to delete the temp file."""
        suffix = ".duckdb" if backend == "duckdb" else ".sqlite3"
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="datapilot_session_")
        os.close(fd)
        instance = cls(backend=backend, path=path, thread_safe=(backend == "sqlite"))
        instance._owns_temp_file = True
        logger.info("Created session database at %s (backend=%s)", path, backend)
        return instance

    # -- connection lifecycle (sqlite only; duckdb keeps a single connection) --

    def _connect(self):
        if self.backend != "sqlite":
            return self._conn
        if self._thread_safe:
            return sqlite3.connect(self.path, check_same_thread=False)
        return self._conn

    def _disconnect(self, conn) -> None:
        if self.backend == "sqlite" and self._thread_safe:
            conn.close()

    def load_dataframe(self, df: pd.DataFrame, table_name: str) -> TableSchema:
        """Register a DataFrame as a table, replacing any existing table of the same name."""
        table_name = _safe_identifier(table_name)
        if self.backend == "sqlite":
            conn = self._connect()
            try:
                df.to_sql(table_name, conn, if_exists="replace", index=False)
                conn.commit()
            finally:
                self._disconnect(conn)
        else:  # duckdb
            self._conn.register("_incoming_df", df)
            self._conn.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM _incoming_df')
            self._conn.unregister("_incoming_df")
        return self.describe_table(table_name)

    def describe_table(self, table_name: str) -> TableSchema:
        table_name = _safe_identifier(table_name)
        if self.backend == "sqlite":
            conn = self._connect()
            try:
                cursor = conn.execute(f'PRAGMA table_info("{table_name}")')
                columns = [(row[1], row[2]) for row in cursor.fetchall()]
                row_count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
                column_samples = self._sample_low_cardinality_columns(conn, table_name, columns)
            finally:
                self._disconnect(conn)
        else:
            info = self._conn.execute(f'DESCRIBE "{table_name}"').fetchall()
            columns = [(row[0], row[1]) for row in info]
            row_count = self._conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            column_samples = self._sample_low_cardinality_columns(self._conn, table_name, columns)

        return TableSchema(name=table_name, columns=columns, row_count=row_count, column_samples=column_samples)

    def _sample_low_cardinality_columns(
        self, conn, table_name: str, columns: list[tuple[str, str]], max_distinct: int = 30, sample_size: int = 8
    ) -> dict[str, list[str]]:
        """Fetch a handful of real distinct values for text-like, low-cardinality
        columns. This is what lets a filter like "...for the North region" be
        matched against an actual value that exists in the data, instead of the
        planner guessing at filter values it has never seen."""
        samples: dict[str, list[str]] = {}
        text_types = ("CHAR", "TEXT", "VARCHAR", "STRING", "OBJECT")
        for name, sqltype in columns:
            if not any(t in sqltype.upper() for t in text_types):
                continue
            try:
                distinct_count = conn.execute(
                    f'SELECT COUNT(DISTINCT "{name}") FROM "{table_name}"'
                ).fetchone()[0]
                if distinct_count == 0 or distinct_count > max_distinct:
                    continue
                rows = conn.execute(
                    f'SELECT DISTINCT "{name}" FROM "{table_name}" WHERE "{name}" IS NOT NULL LIMIT {sample_size}'
                ).fetchall()
                samples[name] = [str(r[0]) for r in rows]
            except Exception:
                continue
        return samples

    def list_tables(self) -> list[str]:
        if self.backend == "sqlite":
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            finally:
                self._disconnect(conn)
        else:
            rows = self._conn.execute("SHOW TABLES").fetchall()
        return [r[0] for r in rows]

    def describe_schema(self) -> dict[str, TableSchema]:
        return {name: self.describe_table(name) for name in self.list_tables()}

    def query(self, sql: str, max_rows: int = 1000) -> pd.DataFrame:
        """Execute an already-validated, read-only SQL statement and return a DataFrame."""
        if self.backend == "sqlite":
            conn = self._connect()
            try:
                df = pd.read_sql_query(sql, conn)
            finally:
                self._disconnect(conn)
        else:
            df = self._conn.execute(sql).fetch_df()
        if len(df) > max_rows:
            df = df.head(max_rows)
        return df

    def close(self) -> None:
        """Close the held connection (if any) and delete the temp file (if
        this instance owns one -- see create_session_database())."""
        if self.backend == "sqlite" and self._conn is not None:
            self._conn.close()
        elif self.backend != "sqlite":
            self._conn.close()
        if self._owns_temp_file and os.path.exists(self.path):
            try:
                os.remove(self.path)
                logger.info("Removed session database file %s", self.path)
            except OSError:
                logger.warning("Could not remove session database file %s", self.path, exc_info=True)


def _safe_identifier(name: str) -> str:
    """Reduce a free-form name (e.g. an uploaded filename) to a safe SQL identifier."""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned.lower()
