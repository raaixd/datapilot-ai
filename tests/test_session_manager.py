"""
Tests for app/api/session_manager.py -- the fix for cross-session data
leakage in the FastAPI backend (see that module's docstring: previously
ALL API clients shared one global dataset). This is fully testable despite
fastapi itself not being installed, because session_manager.py has zero
dependency on it.
"""

import time
import unittest

import pandas as pd

from app.api.session_manager import SessionManager, SessionNotFoundError
from app.core.config import Settings
from app.llm.mock_client import MockLLMClient


def _manager(ttl_seconds: int = 3600) -> SessionManager:
    settings = Settings(llm_provider="mock", database_backend="sqlite")
    return SessionManager(MockLLMClient(), settings, ttl_seconds=ttl_seconds)


class TestSessionIsolation(unittest.TestCase):
    def test_two_sessions_get_different_ids_and_databases(self):
        manager = _manager()
        s1 = manager.create_session()
        s2 = manager.create_session()
        try:
            self.assertNotEqual(s1.session_id, s2.session_id)
            self.assertNotEqual(s1.db.path, s2.db.path)
        finally:
            manager.close_all()

    def test_data_uploaded_to_one_session_not_visible_in_another(self):
        """This IS the exact bug fix: client A's data must never appear
        when client B queries their own session."""
        manager = _manager()
        session_a = manager.create_session()
        session_b = manager.create_session()
        try:
            session_a.db.load_dataframe(pd.DataFrame({"revenue": [100.0]}), "sales")

            self.assertIn("sales", session_a.db.list_tables())
            self.assertNotIn("sales", session_b.db.list_tables())

            result_b = session_b.orchestrator.analyze("What is the total revenue?")
            self.assertFalse(result_b.success)
            self.assertIn("no dataset", (result_b.error or "").lower())

            result_a = session_a.orchestrator.analyze("What is the total revenue?")
            self.assertTrue(result_a.success, result_a.error)
        finally:
            manager.close_all()

    def test_get_session_returns_the_same_session(self):
        manager = _manager()
        created = manager.create_session()
        try:
            fetched = manager.get_session(created.session_id)
            self.assertIs(fetched, created)
        finally:
            manager.close_all()

    def test_get_unknown_session_raises_clear_error(self):
        manager = _manager()
        with self.assertRaises(SessionNotFoundError) as ctx:
            manager.get_session("not-a-real-session-id")
        self.assertIn("was not found", str(ctx.exception))
        manager.close_all()


class TestSessionExpiry(unittest.TestCase):
    def test_expired_session_is_pruned_and_unreachable(self):
        manager = _manager(ttl_seconds=0)  # expires immediately
        state = manager.create_session()
        time.sleep(0.01)
        with self.assertRaises(SessionNotFoundError):
            manager.get_session(state.session_id)

    def test_prune_expired_closes_and_removes_temp_files(self):
        import os

        manager = _manager(ttl_seconds=0)
        state = manager.create_session()
        path = state.db.path
        self.assertTrue(os.path.exists(path))
        time.sleep(0.01)
        pruned_count = manager.prune_expired()
        self.assertEqual(pruned_count, 1)
        self.assertFalse(os.path.exists(path))

    def test_active_session_still_reachable_within_ttl(self):
        manager = _manager(ttl_seconds=3600)
        state = manager.create_session()
        try:
            fetched = manager.get_session(state.session_id)  # should not raise
            self.assertEqual(fetched.session_id, state.session_id)
        finally:
            manager.close_all()


class TestSessionLifecycle(unittest.TestCase):
    def test_delete_session_removes_it_and_closes_db(self):
        import os

        manager = _manager()
        state = manager.create_session()
        path = state.db.path
        deleted = manager.delete_session(state.session_id)
        self.assertTrue(deleted)
        self.assertFalse(os.path.exists(path))
        with self.assertRaises(SessionNotFoundError):
            manager.get_session(state.session_id)

    def test_delete_unknown_session_returns_false_not_error(self):
        manager = _manager()
        self.assertFalse(manager.delete_session("does-not-exist"))
        manager.close_all()

    def test_active_session_count(self):
        manager = _manager()
        self.assertEqual(manager.active_session_count(), 0)
        manager.create_session()
        manager.create_session()
        self.assertEqual(manager.active_session_count(), 2)
        manager.close_all()

    def test_close_all_removes_every_session(self):
        manager = _manager()
        manager.create_session()
        manager.create_session()
        manager.close_all()
        self.assertEqual(manager.active_session_count(), 0)

    def test_session_touch_updates_last_accessed(self):
        manager = _manager()
        state = manager.create_session()
        first = state.last_accessed_at
        time.sleep(0.01)
        manager.get_session(state.session_id)
        self.assertGreater(state.last_accessed_at, first)
        manager.close_all()


if __name__ == "__main__":
    unittest.main()
