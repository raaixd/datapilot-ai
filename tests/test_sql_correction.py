"""
Unit tests for the self-correcting SQL execution loop in app/agents/orchestrator.py.

Tests:
1. Successful first execution (no retry needed)
2. Correctable SQL failure resolved on retry
3. Retry exhaustion (fails after 2 retries)
4. Invalid regenerated SQL rejected by validator
5. Destructive SQL (e.g. DROP, UPDATE) attempted during correction rejected
"""

import unittest
from unittest.mock import MagicMock

import pandas as pd

from app.agents.orchestrator import Orchestrator
from app.data.database import AnalyticalDatabase
from app.llm.base import LLMClient


class CustomMockClient(LLMClient):
    """Configurable mock client to simulate planner and SQL generator behavior."""

    def __init__(self, plan_json: str, initial_sql: str, corrected_sqls: list[str] | None = None):
        self.plan_json = plan_json
        self.initial_sql = initial_sql
        self.corrected_sqls = list(corrected_sqls or [])
        self.correction_calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if "TASK: plan" in user_prompt:
            return self.plan_json
        if "TASK: sql" in user_prompt:
            return self.initial_sql
        if "TASK: correct_sql" in user_prompt:
            self.correction_calls += 1
            if self.corrected_sqls:
                return self.corrected_sqls.pop(0)
            return self.initial_sql
        if "TASK: insight" in user_prompt:
            return "Analysis completed successfully."
        return ""


class TestSQLCorrectionLoop(unittest.TestCase):
    def setUp(self):
        self.db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        self.df = pd.DataFrame(
            {
                "region": ["North", "South", "East"],
                "revenue": [100.0, 200.0, 300.0],
            }
        )
        self.db.load_dataframe(self.df, "sales")
        self.plan_json = """
        {
            "intent": "aggregation",
            "table": "sales",
            "metric_column": "revenue",
            "aggregation": "sum",
            "dimension_column": null,
            "date_column": null,
            "filters": [],
            "chart_type": "table",
            "sort_desc": true,
            "clarification_needed": null
        }
        """

    def tearDown(self):
        self.db.close()

    def test_successful_first_execution_requires_no_retries(self):
        llm = CustomMockClient(
            plan_json=self.plan_json,
            initial_sql='SELECT SUM("revenue") AS value FROM "sales"',
        )
        orchestrator = Orchestrator(self.db, llm)
        result = orchestrator.analyze("What is the total revenue?")

        self.assertTrue(result.success)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(len(result.correction_history), 0)
        self.assertEqual(llm.correction_calls, 0)

    def test_correctable_sql_failure_succeeds_on_retry(self):
        # Initial SQL references a non-existent column "rev" that passes broad regex validation
        # but fails SQLite execution with "no such column: rev".
        # Correction generates valid SQL 'SELECT SUM("revenue") AS value FROM "sales"'.
        llm = CustomMockClient(
            plan_json=self.plan_json,
            initial_sql='SELECT "revenue" AS rev, SUM("revenue") AS value FROM "sales"',  # execution error injection
            corrected_sqls=['SELECT SUM("revenue") AS value FROM "sales"'],
        )
        orchestrator = Orchestrator(self.db, llm)

        # Mock the first query to raise an operational error, and succeed on second
        original_query = self.db.query
        query_attempts = 0

        def failing_first_query(sql, max_rows=1000):
            nonlocal query_attempts
            query_attempts += 1
            if query_attempts == 1:
                raise Exception("sqlite3.OperationalError: no such column: rev")
            return original_query(sql, max_rows=max_rows)

        self.db.query = failing_first_query
        try:
            result = orchestrator.analyze("What is the total revenue?")
            self.assertTrue(result.success)
            self.assertEqual(result.retry_count, 1)
            self.assertEqual(len(result.correction_history), 1)
            self.assertEqual(llm.correction_calls, 1)
        finally:
            self.db.query = original_query

    def test_retry_exhaustion_stops_after_two_retries(self):
        llm = CustomMockClient(
            plan_json=self.plan_json,
            initial_sql='SELECT SUM("revenue") AS value FROM "sales"',
            corrected_sqls=[
                'SELECT SUM("revenue") AS value FROM "sales"',
                'SELECT SUM("revenue") AS value FROM "sales"',
                'SELECT SUM("revenue") AS value FROM "sales"',
            ],
        )
        orchestrator = Orchestrator(self.db, llm)

        # Database persistently throws
        self.db.query = MagicMock(side_effect=Exception("Persistent database lock error"))

        result = orchestrator.analyze("What is the total revenue?")
        self.assertFalse(result.success)
        self.assertEqual(result.retry_count, 2)
        self.assertEqual(len(result.correction_history), 3)  # initial attempt + 2 retries
        self.assertEqual(llm.correction_calls, 2)
        self.assertIn("failed to run", result.error)

    def test_destructive_sql_in_correction_is_rejected_by_validator(self):
        # Initial fails execution, but the LLM tries to generate a destructive statement
        llm = CustomMockClient(
            plan_json=self.plan_json,
            initial_sql='SELECT SUM("revenue") AS value FROM "sales"',
            corrected_sqls=['DROP TABLE "sales"; SELECT 1;'],
        )
        orchestrator = Orchestrator(self.db, llm)

        original_query = self.db.query

        def fail_once(sql, max_rows=1000):
            raise Exception("Mock query failure")

        self.db.query = fail_once
        try:
            result = orchestrator.analyze("What is the total revenue?")
            self.assertFalse(result.success)
            self.assertIn("safety checks", result.error.lower())
            self.assertIn("SQL correction validation errors", result.debug_info)
        finally:
            self.db.query = original_query

    def test_prompt_injection_during_correction_is_rejected(self):
        llm = CustomMockClient(
            plan_json=self.plan_json,
            initial_sql='SELECT SUM("revenue") AS value FROM "sales"',
            corrected_sqls=["SELECT * FROM sqlite_master;"],
        )
        orchestrator = Orchestrator(self.db, llm)

        self.db.query = MagicMock(side_effect=Exception("Mock query failure"))
        result = orchestrator.analyze("What is the total revenue?")
        self.assertFalse(result.success)
        self.assertIn("safety checks", result.error.lower())


if __name__ == "__main__":
    unittest.main()
