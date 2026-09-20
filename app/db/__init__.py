"""
Application database and persistence layer for DataPilot AI.

Provides SQLAlchemy 2.0 models, database engine/session lifecycle management,
and repository abstractions for PostgreSQL (RDS) and SQLite (LOCAL_MODE).
"""

from app.db.base import Base
from app.db.session import get_db, get_engine, get_session_factory, init_db

__all__ = ["Base", "get_db", "get_engine", "get_session_factory", "init_db"]
