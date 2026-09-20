"""
Database session and connection management.

Supports both SQLite (local development / testing) and PostgreSQL (AWS RDS).
Configured from Settings.database_url (defaults to sqlite:///data/datapilot_meta.db).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine(database_url: str | None = None) -> Engine:
    """Get or create the SQLAlchemy engine.

    Configures SQLite with check_same_thread=False and foreign key PRAGMA.
    Configures PostgreSQL with pool_pre_ping=True for resilient connections.
    """
    global _engine
    if _engine is not None and database_url is None:
        return _engine

    url = database_url or get_settings().database_url

    engine_kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}

        # Auto-create directory if file-backed SQLite
        if not url.startswith("sqlite:///:memory:"):
            # Format: sqlite:///path/to/db
            path_part = url.replace("sqlite:///", "")
            # If relative path, resolve parent
            parent_dir = Path(path_part).parent
            if parent_dir and not parent_dir.exists():
                parent_dir.mkdir(parents=True, exist_ok=True)

        engine = create_engine(url, **engine_kwargs)

        # Enable foreign key support in SQLite
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    else:
        # PostgreSQL / RDS configuration
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "10"))
        engine_kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "20"))
        engine = create_engine(url, **engine_kwargs)

    if database_url is None:
        _engine = engine

    return engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Get or create the session factory bound to the engine."""
    global _SessionFactory
    eng = engine or get_engine()
    if _SessionFactory is None or engine is not None:
        factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
        if engine is None:
            _SessionFactory = factory
        return factory
    return _SessionFactory


def init_db(engine: Engine | None = None) -> None:
    """Initialize the database schema (create all tables)."""
    eng = engine or get_engine()
    # Import models to ensure they are registered with Base.metadata
    import app.db.models  # noqa: F401

    Base.metadata.create_all(bind=eng)
    logger.info("Initialized database tables with bind=%s", eng.url)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for obtaining a database session per request."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Generator[Session, None, None]:
    """Context manager for transactional database operations.

    Automatically commits on normal exit and rolls back on exception.
    """
    fac = factory or get_session_factory()
    session = fac()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
