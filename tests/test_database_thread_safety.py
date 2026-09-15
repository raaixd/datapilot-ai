"""
Tests for app/data/database.py's thread-safety fix.

test_default_memory_mode_is_not_thread_safe below deliberately REPRODUCES
the exact reported failure mode first ("SQLite objects created in a thread
can only be used in that same thread") to prove it's real, then the rest of
this file proves create_session_database() fixes it -- across real
threading.Thread workers, not just a code-review argument.
"""
import os
import threading
import unittest

import pandas as pd

from app.data.database import AnalyticalDatabase


class TestDefaultModeIsSingleThreaded(unittest.TestCase):
    def test_default_memory_mode_is_not_thread_safe(self):
        """Reproduces the exact bug report: a connection created on the main
        thread, queried from a different thread, raises sqlite3.ProgrammingError.
        This documents WHY create_session_database() exists -- the default
        mode is intentionally NOT meant for multi-threaded use (see the
        module docstring's alternatives-considered writeup)."""
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(pd.DataFrame({"a": [1, 2, 3]}), "t")

        errors = []

        def query_from_other_thread():
            try:
                db.query('SELECT * FROM "t"')
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=query_from_other_thread)
        t.start()
        t.join()

        self.assertEqual(len(errors), 1)
        self.assertIn("SQLite objects created in a thread", str(errors[0]))


class TestSessionDatabaseThreadSafety(unittest.TestCase):
    def test_load_on_main_thread_query_on_worker_thread(self):
        """The exact scenario Streamlit can trigger: load happens on one
        thread, a subsequent query happens on a different one."""
        db = AnalyticalDatabase.create_session_database()
        try:
            db.load_dataframe(pd.DataFrame({"revenue": [10.0, 20.0, 30.0]}), "sales")

            results = []
            errors = []

            def query_from_worker():
                try:
                    df = db.query('SELECT SUM(revenue) AS total FROM "sales"')
                    results.append(df.iloc[0]["total"])
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            t = threading.Thread(target=query_from_worker)
            t.start()
            t.join()

            self.assertEqual(errors, [])
            self.assertEqual(results, [60.0])
        finally:
            db.close()

    def test_many_concurrent_readers_do_not_corrupt_or_error(self):
        """20 threads querying the same session database concurrently --
        each opens and closes its own short-lived connection (see
        AnalyticalDatabase._connect/_disconnect), so there's no shared
        connection object for threads to race on."""
        db = AnalyticalDatabase.create_session_database()
        try:
            df = pd.DataFrame({"region": ["North", "South"] * 50, "revenue": list(range(100))})
            db.load_dataframe(df, "sales")

            errors = []
            totals = []
            lock = threading.Lock()

            def worker():
                try:
                    result = db.query('SELECT COUNT(*) AS n FROM "sales"')
                    with lock:
                        totals.append(int(result.iloc[0]["n"]))
                except Exception as exc:  # noqa: BLE001
                    with lock:
                        errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [], f"Errors from concurrent readers: {errors}")
            self.assertEqual(totals, [100] * 20)
        finally:
            db.close()

    def test_write_then_immediate_read_from_different_thread_sees_data(self):
        """Guards against a subtler bug: a file-backed DB with per-call
        connections must still make a write from one connection visible to
        a read from a brand-new connection on another thread immediately
        after (no stale-read / uncommitted-write issue)."""
        db = AnalyticalDatabase.create_session_database()
        try:
            def loader():
                db.load_dataframe(pd.DataFrame({"x": [1, 2, 3, 4, 5]}), "t")

            loader_thread = threading.Thread(target=loader)
            loader_thread.start()
            loader_thread.join()

            reader_result = {}

            def reader():
                reader_result["count"] = len(db.query('SELECT * FROM "t"'))

            reader_thread = threading.Thread(target=reader)
            reader_thread.start()
            reader_thread.join()

            self.assertEqual(reader_result["count"], 5)
        finally:
            db.close()


class TestSessionDatabaseFileLifecycle(unittest.TestCase):
    def test_creates_a_real_temp_file(self):
        db = AnalyticalDatabase.create_session_database()
        try:
            self.assertTrue(os.path.exists(db.path))
            self.assertNotEqual(db.path, ":memory:")
        finally:
            db.close()

    def test_close_removes_the_temp_file(self):
        db = AnalyticalDatabase.create_session_database()
        path = db.path
        self.assertTrue(os.path.exists(path))
        db.close()
        self.assertFalse(os.path.exists(path))

    def test_two_session_databases_get_different_files(self):
        """This IS the session-isolation mechanism (see README) -- two
        sessions can never accidentally share a path."""
        db1 = AnalyticalDatabase.create_session_database()
        db2 = AnalyticalDatabase.create_session_database()
        try:
            self.assertNotEqual(db1.path, db2.path)
        finally:
            db1.close()
            db2.close()

    def test_data_in_one_session_database_not_visible_in_another(self):
        db1 = AnalyticalDatabase.create_session_database()
        db2 = AnalyticalDatabase.create_session_database()
        try:
            db1.load_dataframe(pd.DataFrame({"a": [1]}), "t")
            self.assertIn("t", db1.list_tables())
            self.assertNotIn("t", db2.list_tables())
        finally:
            db1.close()
            db2.close()

    def test_default_memory_mode_still_works_unchanged(self):
        # Backward compatibility: tests/eval/CLI all rely on the plain
        # :memory: single-connection mode continuing to work exactly as before.
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(pd.DataFrame({"a": [1, 2, 3]}), "t")
        result = db.query('SELECT COUNT(*) AS n FROM "t"')
        self.assertEqual(result.iloc[0]["n"], 3)
        db.close()  # should not raise, and :memory: mode has no temp file to remove

    def test_uploading_a_second_dataset_without_dropping_leaves_both_tables(self):
        """Documents the latent bug drop_all_tables() exists to let callers
        avoid: loading a second dataset under a different table name does
        NOT replace the first -- both tables end up present, and which one
        a caller's `next(iter(schema))` picks becomes non-deterministic
        (dict insertion order, not user intent). Streamlit's uploader and
        the API's /upload now call drop_all_tables() first specifically to
        avoid this."""
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(pd.DataFrame({"revenue": [1.0]}), "first_upload")
        db.load_dataframe(pd.DataFrame({"sales_amount": [2.0]}), "second_upload")
        self.assertEqual(set(db.list_tables()), {"first_upload", "second_upload"})
        db.close()

    def test_drop_all_tables_leaves_a_single_active_dataset(self):
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(pd.DataFrame({"revenue": [1.0]}), "first_upload")
        db.drop_all_tables()
        db.load_dataframe(pd.DataFrame({"sales_amount": [2.0]}), "second_upload")
        self.assertEqual(db.list_tables(), ["second_upload"])
        db.close()

    def test_drop_all_tables_on_empty_database_does_not_raise(self):
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.drop_all_tables()  # should not raise
        self.assertEqual(db.list_tables(), [])
        db.close()

    def test_drop_all_tables_works_on_session_database(self):
        db = AnalyticalDatabase.create_session_database()
        try:
            db.load_dataframe(pd.DataFrame({"a": [1]}), "t1")
            db.drop_all_tables()
            self.assertEqual(db.list_tables(), [])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
