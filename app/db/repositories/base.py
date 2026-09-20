"""
Base repository providing common session access.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


class BaseRepository:
    """Base class for database repositories."""

    def __init__(self, session: Session):
        self._session = session

    @property
    def session(self) -> Session:
        return self._session
