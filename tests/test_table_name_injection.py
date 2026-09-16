"""
Security regression tests for table-identifier sanitization.

app/data/database.py interpolates `table_name` directly into f-string SQL
(SQLite has no parameterized-identifier syntax -- only parameterized
VALUES -- so manual sanitization via _safe_identifier() is the standard,
correct approach for identifiers). This file exists to PROVE that
sanitization actually holds against injection attempts in a table name
derived from user input (e.g. an uploaded filename), rather than just
assuming it because the code "looks" safe.
"""

import unittest

import pandas as pd

from app.data.database import AnalyticalDatabase, _safe_identifier


class TestSafeIdentifierSanitization(unittest.TestCase):
    def test_quote_and_semicolon_characters_are_stripped(self):
        result = _safe_identifier('sales"; DROP TABLE sales; --')
        self.assertNotIn('"', result)
        self.assertNotIn(";", result)
        self.assertNotIn("'", result)

    def test_union_injection_attempt_neutralized(self):
        result = _safe_identifier('sales" UNION SELECT * FROM sqlite_master --')
        self.assertNotIn('"', result)

    def test_path_traversal_characters_stripped(self):
        result = _safe_identifier("../../etc/passwd")
        self.assertNotIn("/", result)
        self.assertNotIn(".", result)

    def test_empty_name_produces_valid_identifier(self):
        result = _safe_identifier("")
        self.assertTrue(result)  # non-empty
        self.assertFalse(result[0].isdigit())

    def test_leading_digit_gets_prefixed(self):
        result = _safe_identifier("123numeric")
        self.assertFalse(result[0].isdigit())

    def test_result_is_always_alphanumeric_and_underscore_only(self):
        for name in ['sales"; DROP TABLE sales; --', "../../etc/passwd", "normal_name", "Sales Report.csv"]:
            result = _safe_identifier(name)
            self.assertTrue(all(c.isalnum() or c == "_" for c in result), f"{name!r} -> {result!r}")


class TestTableNameInjectionEndToEnd(unittest.TestCase):
    """Proves the sanitization holds through the full load_dataframe ->
    query round trip, not just at the sanitization function in isolation."""

    def test_malicious_table_name_does_not_execute_injected_sql(self):
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        try:
            schema = db.load_dataframe(pd.DataFrame({"a": [1, 2, 3]}), 'sales"; DROP TABLE sales; --')
            # The table was created under a SANITIZED name, not the raw malicious string
            self.assertNotIn(";", schema.name)
            self.assertNotIn('"', schema.name)
            # It exists and is queryable normally -- nothing was dropped or corrupted
            result = db.query(f'SELECT COUNT(*) AS n FROM "{schema.name}"')
            self.assertEqual(result.iloc[0]["n"], 3)
        finally:
            db.close()

    def test_two_different_malicious_names_do_not_collide_destructively(self):
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        try:
            db.load_dataframe(pd.DataFrame({"a": [1]}), 'x"; DROP TABLE sales; --')
            db.load_dataframe(pd.DataFrame({"b": [2]}), "normal_table")
            self.assertIn("normal_table", db.list_tables())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
