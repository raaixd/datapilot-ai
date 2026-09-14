import unittest

from app.agents.sql_validator import validate_sql
from app.data.database import TableSchema

SCHEMA = {
    "sales": TableSchema(
        name="sales",
        columns=[("region", "TEXT"), ("revenue", "REAL"), ("order_date", "TEXT")],
        row_count=100,
    )
}


class TestSQLValidator(unittest.TestCase):
    def test_valid_select_passes(self):
        result = validate_sql('SELECT region, SUM(revenue) AS value FROM sales GROUP BY region', SCHEMA)
        self.assertTrue(result.is_valid)
        self.assertIn("LIMIT", result.safe_sql)

    def test_empty_sql_rejected(self):
        result = validate_sql("", SCHEMA)
        self.assertFalse(result.is_valid)

    def test_rejects_insert(self):
        result = validate_sql("INSERT INTO sales (region) VALUES ('X')", SCHEMA)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("select" in e.lower() for e in result.errors))

    def test_rejects_insert_disguised_as_select_prefix(self):
        # Even if it were reached past the SELECT/WITH check, INSERT is also
        # on the forbidden-keyword list -- belt and suspenders.
        result = validate_sql("SELECT 1; INSERT INTO sales (region) VALUES ('X')", SCHEMA)
        self.assertFalse(result.is_valid)

    def test_rejects_delete(self):
        result = validate_sql("DELETE FROM sales WHERE region = 'North'", SCHEMA)
        self.assertFalse(result.is_valid)

    def test_rejects_drop_table(self):
        result = validate_sql("DROP TABLE sales", SCHEMA)
        self.assertFalse(result.is_valid)

    def test_rejects_multiple_statements(self):
        result = validate_sql("SELECT * FROM sales; DROP TABLE sales;", SCHEMA)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("multiple" in e.lower() for e in result.errors))

    def test_allows_single_trailing_semicolon(self):
        result = validate_sql("SELECT * FROM sales;", SCHEMA)
        self.assertTrue(result.is_valid)

    def test_rejects_system_table_access(self):
        result = validate_sql("SELECT * FROM sqlite_master", SCHEMA)
        self.assertFalse(result.is_valid)

    def test_rejects_union_injection(self):
        result = validate_sql(
            "SELECT region FROM sales UNION SELECT name FROM sqlite_master", SCHEMA
        )
        self.assertFalse(result.is_valid)

    def test_rejects_unknown_table(self):
        result = validate_sql("SELECT * FROM customers", SCHEMA)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("unknown table" in e.lower() for e in result.errors))

    def test_auto_adds_limit_when_missing(self):
        result = validate_sql("SELECT * FROM sales", SCHEMA, max_result_rows=50)
        self.assertTrue(result.is_valid)
        self.assertIn("LIMIT 50", result.safe_sql)

    def test_caps_excessive_limit(self):
        result = validate_sql("SELECT * FROM sales LIMIT 999999", SCHEMA, max_result_rows=100)
        self.assertTrue(result.is_valid)
        self.assertIn("LIMIT 100", result.safe_sql)

    def test_does_not_add_second_limit_when_reasonable(self):
        result = validate_sql("SELECT * FROM sales LIMIT 10", SCHEMA, max_result_rows=1000)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.safe_sql.count("LIMIT"), 1)

    def test_forbidden_keyword_as_substring_of_column_not_blocked(self):
        # "updates" contains "update" as a substring but is a distinct token;
        # the validator must match whole tokens only.
        schema = {"logs": TableSchema(name="logs", columns=[("updates", "INT")], row_count=1)}
        result = validate_sql("SELECT updates FROM logs", schema)
        self.assertTrue(result.is_valid)

    def test_with_cte_select_allowed(self):
        result = validate_sql(
            "WITH t AS (SELECT region, revenue FROM sales) SELECT region FROM t", SCHEMA
        )
        # Fixed in round 2: CTE names are now recognized as valid references,
        # while the base table used *inside* the CTE body is still validated
        # against the real schema (see test_cte_with_unknown_base_table_rejected).
        self.assertTrue(result.is_valid, result.errors)

    def test_cte_with_unknown_base_table_rejected(self):
        result = validate_sql(
            "WITH t AS (SELECT region, revenue FROM customers) SELECT region FROM t", SCHEMA
        )
        self.assertFalse(result.is_valid)
        self.assertTrue(any("unknown table" in e.lower() for e in result.errors))

    def test_multiple_ctes_allowed(self):
        result = validate_sql(
            "WITH a AS (SELECT region FROM sales), b AS (SELECT revenue FROM sales) "
            "SELECT * FROM a, b",
            SCHEMA,
        )
        self.assertTrue(result.is_valid, result.errors)

    def test_rejects_load_extension(self):
        result = validate_sql("SELECT load_extension('evil.so')", SCHEMA)
        self.assertFalse(result.is_valid)

    def test_rejects_readfile(self):
        result = validate_sql("SELECT readfile('/etc/passwd')", SCHEMA)
        self.assertFalse(result.is_valid)

    def test_rejects_pragma(self):
        result = validate_sql("PRAGMA table_info(sales)", SCHEMA)
        self.assertFalse(result.is_valid)


if __name__ == "__main__":
    unittest.main()
