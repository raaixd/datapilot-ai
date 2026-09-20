"""
Tests for Stage 3: AI Analyst Engine, Self-Correcting SQL & Result Grounding.
"""

import unittest
from unittest.mock import MagicMock

import pandas as pd

from app.agents.orchestrator import Orchestrator, _clean_insight, classify_db_error
from app.data.database import AnalyticalDatabase
from app.data.profiler import DataProfiler
from app.data.semantic_schema import extract_semantic_schema
from app.llm.base import LLMClient
from app.llm.mock_client import MockLLMClient


class CustomMockClient(LLMClient):
    def __init__(self, plan_json: str, initial_sql: str, narration: str = "Test insight"):
        self.plan_json = plan_json
        self.initial_sql = initial_sql
        self.narration = narration
        self.last_planner_prompt = None
        self.last_sql_prompt = None

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if "TASK: plan" in user_prompt:
            self.last_planner_prompt = user_prompt
            return self.plan_json
        if "TASK: sql" in user_prompt:
            self.last_sql_prompt = user_prompt
            return self.initial_sql
        if "TASK: correct_sql" in user_prompt:
            return self.initial_sql
        if "TASK: insight" in user_prompt:
            return self.narration
        return ""


class TestStage3AnalystEngine(unittest.TestCase):
    def setUp(self):
        self.db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        self.df = pd.DataFrame({
            "order_id": ["ORD-1", "ORD-2", "ORD-3"],
            "region": ["North", "South", "East"],
            "revenue": [150.0, 250.0, 350.0],
        })
        self.db.load_dataframe(self.df, "sales")
        self.profiler = DataProfiler()
        self.profile = self.profiler.profile(self.df, "sales")
        self.semantic_schema = extract_semantic_schema(self.df, self.profile, "sales")

    def tearDown(self):
        self.db.close()

    def test_classify_db_error(self):
        self.assertEqual(classify_db_error("sqlite3.OperationalError: no such column: foo"), "no_such_column")
        self.assertEqual(classify_db_error("duckdb.BinderException: Referenced column 'bar' not found"), "no_such_column")
        self.assertEqual(classify_db_error("sqlite3.OperationalError: no such table: non_existent"), "no_such_table")
        self.assertEqual(classify_db_error("sqlite3.OperationalError: near 'WHERE': syntax error"), "syntax_error")
        self.assertEqual(classify_db_error("Conversion Error: Could not convert string to float"), "type_mismatch")
        self.assertEqual(classify_db_error("misuse of aggregate function without GROUP BY"), "aggregation_error")
        self.assertEqual(classify_db_error("Some random obscure OS error"), "unknown_error")

    def test_clean_insight_strips_chain_of_thought(self):
        raw = "<think>Let me think step by step about the revenue.</think>Total revenue across all regions is $750."
        cleaned = _clean_insight(raw)
        self.assertEqual(cleaned, "Total revenue across all regions is $750.")
        self.assertNotIn("<think>", cleaned)

    def test_semantic_schema_injected_into_planner_and_sql_generator(self):
        plan_json = """
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
        client = CustomMockClient(plan_json, 'SELECT SUM("revenue") AS value FROM "sales"')
        orchestrator = Orchestrator(self.db, client)

        result = orchestrator.analyze("What is the total revenue?", data_profile=self.profile, semantic_schema=self.semantic_schema)
        self.assertTrue(result.success)
        self.assertIn("semantic_type=currency", client.last_planner_prompt)
        self.assertIn("semantic_type=currency", client.last_sql_prompt)

    def test_latency_metrics_tracked_on_success(self):
        orchestrator = Orchestrator(self.db, MockLLMClient())
        result = orchestrator.analyze("What is the total revenue?")

        self.assertTrue(result.success)
        self.assertGreaterEqual(result.total_latency_ms, 0)
        self.assertGreaterEqual(result.sql_execution_time_ms, 0)
        self.assertGreaterEqual(result.llm_latency_ms, 0)

    def test_empty_result_returns_grounded_message_without_hallucination(self):
        plan_json = """
        {
            "intent": "aggregation",
            "table": "sales",
            "metric_column": "revenue",
            "aggregation": "sum",
            "dimension_column": null,
            "date_column": null,
            "filters": [{"column": "region", "op": "=", "value": "West"}],
            "chart_type": "table",
            "sort_desc": true,
            "clarification_needed": null
        }
        """
        # Return a query that returns 0 rows
        sql_with_no_rows = 'SELECT SUM("revenue") AS value FROM "sales" WHERE "region" = \'West\''
        client = CustomMockClient(plan_json, sql_with_no_rows, narration="Hallucinated $9999")
        orchestrator = Orchestrator(self.db, client)

        result = orchestrator.analyze("What is the revenue for West region?")
        self.assertTrue(result.success)
        # Verify it uses the grounded message rather than calling narration or hallucinating
        self.assertIn("No matching records were found", result.insight)
        self.assertNotIn("Hallucinated", result.insight)

    def test_sql_correction_tracks_error_type_and_preserves_ceiling(self):
        plan_json = """
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
        client = CustomMockClient(plan_json, 'SELECT SUM("revenue") AS value FROM "sales"')
        orchestrator = Orchestrator(self.db, client)

        # Force query to raise
        self.db.query = MagicMock(side_effect=Exception("sqlite3.OperationalError: no such column: missing_col"))

        result = orchestrator.analyze("What is total revenue?")
        self.assertFalse(result.success)
        self.assertEqual(result.retry_count, 2)
        self.assertEqual(result.sql_error_type, "no_such_column")
        self.assertEqual(len(result.correction_history), 3)
        for entry in result.correction_history:
            self.assertEqual(entry["error_type"], "no_such_column")


if __name__ == "__main__":
    unittest.main()
