import unittest

import pandas as pd

from app.agents.orchestrator import Orchestrator
from app.data.database import AnalyticalDatabase
from app.data.profiler import DataProfiler
from app.llm.mock_client import MockLLMClient


def _sample_df():
    return pd.DataFrame({
        "order_date": ["2024-01-01", "2024-01-15", "2024-02-01", "2024-02-20", "2024-03-05"],
        "region": ["North", "South", "North", "East", "South"],
        "product_category": ["Electronics", "Apparel", "Electronics", "Books", "Apparel"],
        "revenue": [100.0, 250.0, 300.0, 50.0, 400.0],
    })


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        self.db.load_dataframe(_sample_df(), "sales")
        self.orchestrator = Orchestrator(self.db, MockLLMClient(), max_result_rows=1000)
        self.profiler = DataProfiler()

    def test_total_revenue_question_succeeds(self):
        result = self.orchestrator.analyze("What is the total revenue?")
        self.assertTrue(result.success, result.error)
        self.assertIn("total_revenue", result.metrics)
        self.assertEqual(result.metrics["total_revenue"], 1100.0)
        self.assertIsNotNone(result.insight)

    def test_revenue_by_region_ranking(self):
        result = self.orchestrator.analyze("What is the total revenue by region?")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.metrics.get("top_entry"), "South")

    def test_trend_question_over_time(self):
        result = self.orchestrator.analyze("Show me the monthly revenue trend")
        self.assertTrue(result.success, result.error)
        self.assertIn(result.metrics.get("trend_direction"), {"increasing", "decreasing", "flat"})

    def test_empty_dataset_handled_without_crash(self):
        empty_db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        orchestrator = Orchestrator(empty_db, MockLLMClient())
        result = orchestrator.analyze("What is the total revenue?")
        self.assertFalse(result.success)
        self.assertIn("no dataset", result.error.lower())

    def test_empty_question_handled(self):
        result = self.orchestrator.analyze("   ")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Question is empty.")

    def test_question_with_no_matching_numeric_column_is_unsupported(self):
        text_only_db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        text_only_db.load_dataframe(pd.DataFrame({"region": ["North", "South"]}), "regions")
        orchestrator = Orchestrator(text_only_db, MockLLMClient())
        result = orchestrator.analyze("What is the total revenue?")
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_does_not_fabricate_numbers_not_in_result(self):
        # The mock LLM's insight narration only ever echoes computed metrics,
        # so a fabricated number cannot appear in the insight text.
        result = self.orchestrator.analyze("What is the total revenue?")
        self.assertTrue(result.success)
        self.assertIn(str(result.metrics["total_revenue"]), result.insight)

    def test_repeated_identical_queries_are_consistent(self):
        first = self.orchestrator.analyze("What is the total revenue by region?")
        second = self.orchestrator.analyze("What is the total revenue by region?")
        self.assertEqual(first.sql, second.sql)
        self.assertEqual(first.metrics, second.metrics)

    def test_malformed_sql_from_llm_is_rejected_not_executed(self):
        class BadSQLClient(MockLLMClient):
            def _sql(self, user_prompt):  # noqa: ANN001
                return "DROP TABLE sales"

        orchestrator = Orchestrator(self.db, BadSQLClient())
        result = orchestrator.analyze("What is the total revenue?")
        self.assertFalse(result.success)
        self.assertIn("safety validation", result.error)
        # prove the table was NOT actually dropped
        self.assertIn("sales", self.db.list_tables())

    def test_profile_warnings_surface_in_result(self):
        df = pd.DataFrame({
            "order_date": ["2024-01-01", None],
            "region": ["North", "South"],
            "product_category": ["Electronics", "Apparel"],
            "revenue": [100.0, None],
        })
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        profile = self.profiler.profile(df)
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("What is the total revenue?", data_profile=profile)
        self.assertTrue(result.success)
        self.assertTrue(len(result.data_quality_warnings) > 0)

    # -- round 2: new intents and phrasing -----------------------------------

    def test_ranking_with_explicit_limit(self):
        result = self.orchestrator.analyze("Show the top 2 regions by revenue")
        self.assertTrue(result.success, result.error)
        self.assertIn("LIMIT 2", result.sql)
        self.assertEqual(len(result.result_preview), 2)

    def test_singular_ranking_question_implies_limit_one(self):
        result = self.orchestrator.analyze("Which region has the highest revenue?")
        self.assertTrue(result.success, result.error)
        self.assertIn("LIMIT 1", result.sql)
        self.assertEqual(result.metrics.get("top_entry"), "South")

    def test_descending_order_phrasing_triggers_ranking_not_plain_aggregation(self):
        result = self.orchestrator.analyze("List regions based on revenue in descending order")
        self.assertTrue(result.success, result.error)
        self.assertIn("GROUP BY", result.sql)
        self.assertIn("DESC", result.sql)

    def test_ascending_order_phrasing_sorts_ascending(self):
        result = self.orchestrator.analyze("Show regions ranked from lowest to highest revenue")
        self.assertTrue(result.success, result.error)
        self.assertIn("ASC", result.sql)

    def test_ambiguous_best_selling_question_asks_for_clarification(self):
        # Needs both a revenue-like AND a quantity-like numeric column present
        # for "best-selling" to be genuinely ambiguous -- otherwise there's
        # only one possible interpretation and it should just answer it.
        df = pd.DataFrame({
            "product_category": ["Electronics", "Apparel", "Electronics"],
            "revenue": [500.0, 100.0, 300.0],
            "quantity": [2, 50, 3],
        })
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("What is the best-selling product category?")
        self.assertFalse(result.success)
        self.assertIn("revenue", result.error.lower())
        self.assertIn("units", result.error.lower())

    def test_missing_data_question_answered_from_profile_without_sql(self):
        df = pd.DataFrame({"a": [1, None, 3], "b": ["x", "y", None]})
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        profile = self.profiler.profile(df)
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("How much data is missing?", data_profile=profile)
        self.assertTrue(result.success, result.error)
        self.assertIsNone(result.sql)
        self.assertEqual(result.metrics["columns_with_missing_values"], 2)

    def test_duplicate_analysis_question_answered_from_profile_without_sql(self):
        df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        profile = self.profiler.profile(df)
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("Are there any duplicate rows?", data_profile=profile)
        self.assertTrue(result.success, result.error)
        self.assertIsNone(result.sql)
        self.assertEqual(result.metrics["duplicate_row_count"], 1)

    def test_missing_data_question_without_profile_fails_honestly(self):
        result = self.orchestrator.analyze("How much data is missing?")  # no data_profile passed
        self.assertFalse(result.success)
        self.assertIn("profile", result.error.lower())

    def test_declining_sales_by_dimension(self):
        rows = []
        for month in range(1, 5):
            rows.append({"order_date": f"2024-{month:02d}-01", "region": "North", "product_category": "Electronics", "revenue": 500 - month * 50})
            rows.append({"order_date": f"2024-{month:02d}-01", "region": "South", "product_category": "Apparel", "revenue": 100 + month * 50})
        df = pd.DataFrame(rows)
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("Which regions experienced declining sales?")
        self.assertTrue(result.success, result.error)
        self.assertIn("North", result.metrics["declining_entities"])
        self.assertIn("South", result.metrics["increasing_entities"])

    def test_result_includes_llm_provider_and_does_not_overclaim(self):
        result = self.orchestrator.analyze("What is the total revenue?")
        self.assertEqual(result.llm_provider, "mock")

    def test_result_provider_comes_from_the_client_not_environment(self):
        result = self.orchestrator.analyze("What is the total revenue?")
        self.assertEqual(result.llm_provider, "mock")

    def test_filter_value_with_comma_is_preserved(self):
        df = pd.DataFrame({"customer": ["ACME, Inc", "Other"], "revenue": [10.0, 20.0]})
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        result = Orchestrator(db, MockLLMClient()).analyze("What is the total revenue for ACME, Inc?")
        self.assertTrue(result.success, result.error)
        self.assertIn("'ACME, Inc'", result.sql)
        self.assertEqual(result.metrics["total_revenue"], 10)

    def test_analyze_logs_the_question(self):
        with self.assertLogs("app.agents.orchestrator", level="INFO") as cm:
            self.orchestrator.analyze("What is the total revenue?")
        self.assertTrue(any("analyze() called" in msg for msg in cm.output))

    def test_alternate_schema_dataset_resolves_via_synonyms(self):
        df = pd.DataFrame({
            "transaction_date": ["2024-01-01", "2024-02-01", "2024-03-01"],
            "item_name": ["Notebook", "Backpack", "Notebook"],
            "sales_amount": [50.0, 120.0, 75.0],
            "units_sold": [5, 2, 7],
        })
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "ecommerce")
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("What is the total sales amount?")
        self.assertTrue(result.success, result.error)
        self.assertIn("total_sales_amount", result.metrics)
        self.assertEqual(result.metrics["total_sales_amount"], 245.0)


if __name__ == "__main__":
    unittest.main()
