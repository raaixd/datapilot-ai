"""
Unit tests for PostgreSQL / RDS database connection pool configuration and safety.

Verifies:
  - Connection pooling kwargs for PostgreSQL engines (pool_pre_ping, pool_size, max_overflow, pool_recycle, pool_timeout)
  - session_scope transaction rollback on exception
  - Clean session closing lifecycle
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.db.session import get_engine, session_scope


class TestDatabasePostgres(unittest.TestCase):
    @patch("app.db.session.create_engine")
    @patch("app.db.session.get_settings")
    def test_postgres_engine_pool_configuration(self, mock_get_settings, mock_create_engine):
        mock_settings = Settings(
            database_url="postgresql+psycopg://user:pass@localhost:5432/veridex",
            db_pool_size=15,
            db_max_overflow=25,
            db_pool_recycle=1200,
            db_pool_timeout=45,
        )
        mock_get_settings.return_value = mock_settings

        get_engine(database_url="postgresql+psycopg://user:pass@localhost:5432/veridex")

        mock_create_engine.assert_called_once()
        args, kwargs = mock_create_engine.call_args
        self.assertEqual(args[0], "postgresql+psycopg://user:pass@localhost:5432/veridex")
        self.assertTrue(kwargs.get("pool_pre_ping"))
        self.assertEqual(kwargs.get("pool_size"), 15)
        self.assertEqual(kwargs.get("max_overflow"), 25)
        self.assertEqual(kwargs.get("pool_recycle"), 1200)
        self.assertEqual(kwargs.get("pool_timeout"), 45)

    def test_session_scope_commit_on_success(self):
        mock_session = MagicMock()
        mock_factory = MagicMock(return_value=mock_session)

        with session_scope(factory=mock_factory) as s:
            s.execute = MagicMock()

        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()
        mock_session.rollback.assert_not_called()

    def test_session_scope_rollback_on_exception(self):
        mock_session = MagicMock()
        mock_factory = MagicMock(return_value=mock_session)

        with self.assertRaises(ValueError):
            with session_scope(factory=mock_factory):
                raise ValueError("Intentional transaction failure")

        mock_session.rollback.assert_called_once()
        mock_session.commit.assert_not_called()
        mock_session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
