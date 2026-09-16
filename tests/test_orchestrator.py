import unittest

import pandas as pd

from app.agents.orchestrator import Orchestrator
from app.data.database import AnalyticalDatabase
from app.data.profiler import DataProfiler
from app.llm.mock_client import MockLLMClient


def _sample_df():
    return pd.DataFrame(
        {
            "order_date": ["2024-01-01", "2024-01-15", "2024-02-01", "2024-02-20", "2024-03-05"],
            "region": ["North", "South", "North", "East", "South"],
            "product_category": ["Electronics", "Apparel", "Electronics", "Books", "Apparel"],
            "revenue": [100.0, 250.0, 300.0, 50.0, 400.0],
        }
    )


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
        # User-facing error is friendly, never exposes the raw validator text
        # or SQL to normal users -- the technical detail lives in debug_info.
        self.assertNotIn("SQL validation errors", result.error)
        self.assertIn("SQL validation errors", result.debug_info)
        # prove the table was NOT actually dropped
        self.assertIn("sales", self.db.list_tables())

    def test_profile_warnings_surface_in_result(self):
        df = pd.DataFrame(
            {
                "order_date": ["2024-01-01", None],
                "region": ["North", "South"],
                "product_category": ["Electronics", "Apparel"],
                "revenue": [100.0, None],
            }
        )
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
        df = pd.DataFrame(
            {
                "product_category": ["Electronics", "Apparel", "Electronics"],
                "revenue": [500.0, 100.0, 300.0],
                "quantity": [2, 50, 3],
            }
        )
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
            rows.append(
                {
                    "order_date": f"2024-{month:02d}-01",
                    "region": "North",
                    "product_category": "Electronics",
                    "revenue": 500 - month * 50,
                }
            )
            rows.append(
                {
                    "order_date": f"2024-{month:02d}-01",
                    "region": "South",
                    "product_category": "Apparel",
                    "revenue": 100 + month * 50,
                }
            )
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

    # -- round 3, continued: anomaly detection (declared as a valid intent since
    # round 2 but never actually implemented until now) ------------------------

    def test_anomaly_detection_finds_a_deliberate_outlier(self):
        df = pd.DataFrame({"order_id": [f"O{i}" for i in range(20)], "revenue": [100.0] * 19 + [50000.0]})
        profile = self.profiler.profile(df, dataset_name="test")
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("Are there any anomalies in the revenue?", data_profile=profile)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.plan.intent, "anomaly_detection")
        self.assertEqual(result.metrics["anomaly_count"], 1)
        self.assertEqual(result.result_preview[0]["revenue"], 50000.0)

    def test_anomaly_detection_finds_nothing_in_uniform_data(self):
        df = pd.DataFrame({"revenue": [100.0, 101.0, 99.0, 102.0, 98.0]})
        profile = self.profiler.profile(df, dataset_name="test")
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("Show me outliers in revenue", data_profile=profile)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.metrics["anomaly_count"], 0)

    def test_anomaly_detection_handles_zero_variance_without_crashing(self):
        df = pd.DataFrame({"revenue": [100.0] * 10})
        profile = self.profiler.profile(df, dataset_name="test")
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("Are there any unusual values in revenue?", data_profile=profile)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.metrics["std"], 0)
        self.assertIsNone(result.sql)  # never even built a query -- nothing meaningful to detect against

    def test_anomaly_detection_without_profile_fails_honestly(self):
        result = self.orchestrator.analyze("Are there any anomalies in the revenue?")  # no data_profile passed
        self.assertFalse(result.success)
        self.assertIn("profile", result.error.lower())

    def test_anomaly_detection_sql_is_validated_before_execution(self):
        """The SQL is built internally (not by the LLM), but it must still
        go through the normal safety validator -- proves that path isn't
        skipped just because the LLM didn't write the query."""
        df = pd.DataFrame({"revenue": [100.0] * 19 + [999.0]})
        profile = self.profiler.profile(df, dataset_name="test")
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "sales")
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("Find anomalies in revenue", data_profile=profile)
        self.assertTrue(result.success, result.error)
        self.assertTrue(result.sql.strip().upper().startswith("SELECT"))
        self.assertNotIn(";", result.sql.rstrip(";"))  # single statement, no injection surface

    def test_analyze_logs_the_question(self):
        with self.assertLogs("app.agents.orchestrator", level="INFO") as cm:
            self.orchestrator.analyze("What is the total revenue?")
        self.assertTrue(any("analyze() called" in msg for msg in cm.output))

    def test_alternate_schema_dataset_resolves_via_synonyms(self):
        df = pd.DataFrame(
            {
                "transaction_date": ["2024-01-01", "2024-02-01", "2024-03-01"],
                "item_name": ["Notebook", "Backpack", "Notebook"],
                "sales_amount": [50.0, 120.0, 75.0],
                "units_sold": [5, 2, 7],
            }
        )
        db = AnalyticalDatabase(backend="sqlite", path=":memory:")
        db.load_dataframe(df, "ecommerce")
        orchestrator = Orchestrator(db, MockLLMClient())
        result = orchestrator.analyze("What is the total sales amount?")
        self.assertTrue(result.success, result.error)
        self.assertIn("total_sales_amount", result.metrics)
        self.assertEqual(result.metrics["total_sales_amount"], 245.0)

    # -- round 3: the reported bug, reproduced end-to-end through the full orchestrator --

    def test_meaning_of_life_no_longer_produces_misleading_column_error(self):
        """This is the exact bug reported at the start of round 3: an
        unrelated question ('meaning of life') used to return
        'Could not map this question to a specific measurable column in
        ...' -- a misleading, internals-leaking message for a question that
        has nothing to do with column mapping at all. It must now be
        classified out_of_scope with a friendly, honest message instead."""
        result = self.orchestrator.analyze("What is the meaning of life?")
        self.assertFalse(result.success)
        self.assertEqual(result.scope, "out_of_scope")
        self.assertNotIn("Could not map", result.error)
        self.assertNotIn("measurable column", result.error)
        self.assertIn("outside the scope", result.error)

    def test_out_of_scope_never_exposes_internal_class_names_or_tracebacks(self):
        for q in ["Write me a poem.", "Who is the president?", "Tell me a joke.", "How do I cook pasta?"]:
            with self.subTest(question=q):
                result = self.orchestrator.analyze(q)
                self.assertFalse(result.success)
                self.assertEqual(result.scope, "out_of_scope")
                for leak in ("Traceback", "AnalysisPlan", "PlanValidationError", "Exception", "NoneType"):
                    self.assertNotIn(leak, result.error)

    def test_unsafe_question_refused_with_clear_message_not_generic_error(self):
        result = self.orchestrator.analyze("Drop the sales table.")
        self.assertFalse(result.success)
        self.assertEqual(result.scope, "unsafe")
        self.assertIn("read-only", result.error.lower())
        self.assertIsNone(result.sql)  # never even generated, let alone executed

    def test_ambiguous_vague_phrasing_gets_clarification_with_real_columns(self):
        result = self.orchestrator.analyze("How are sales doing?")
        self.assertFalse(result.success)
        self.assertEqual(result.scope, "ambiguous")
        self.assertTrue(len(result.clarification_options) > 0)
        self.assertTrue(all(opt in ("revenue", "quantity", "unit_price") for opt in result.clarification_options))

    def test_valid_question_still_succeeds_after_scope_gate_added(self):
        # Guards against the scope gate becoming overzealous and rejecting
        # genuinely valid questions -- the primary regression risk of this change.
        # (_sample_df() only has region/product_category/revenue as concrete
        # columns -- no unit_price/quantity -- so these are scoped to what
        # that fixture actually has.)
        for q in [
            "What is the total revenue?",
            "What is the total revenue by product category?",
            "Compare revenue between regions",
            "Show me the monthly revenue trend",
        ]:
            with self.subTest(question=q):
                result = self.orchestrator.analyze(q)
                self.assertTrue(result.success, f"{q!r} should still succeed: {result.error}")
                self.assertEqual(result.scope, "in_scope")

    def test_debug_info_carries_technical_detail_never_shown_in_error(self):
        class BadSQLClient(MockLLMClient):
            def _sql(self, user_prompt):  # noqa: ANN001
                return "DROP TABLE sales"

        orchestrator = Orchestrator(self.db, BadSQLClient())
        result = orchestrator.analyze("What is the total revenue?")
        self.assertFalse(result.success)
        self.assertIsNotNone(result.debug_info)
        self.assertNotEqual(result.error, result.debug_info)

    # -- round 3: natural phrasing variations of "declining sales" ------------
    # (found via direct testing against the real orchestrator -- not the
    # spec's literal example, but realistic rewordings of it that initially
    # failed due to a plural/singular mismatch and an overly strict "by X"
    # group-by heuristic; see CHANGELOG.md)

    def test_declining_sales_plural_category_phrasing(self):
        result = self.orchestrator.analyze("Which categories had falling sales?")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.plan.intent, "trend_by_dimension")
        self.assertEqual(result.plan.dimension_column, "product_category")

    def test_revenue_by_categories_plural_resolves_grouping(self):
        result = self.orchestrator.analyze("Show me revenue by categories")
        self.assertTrue(result.success, result.error)
        self.assertIn("GROUP BY", result.sql)
        self.assertIn("product_category", result.sql)

    def test_declining_with_no_dimension_falls_back_to_trend_not_flat_total(self):
        result = self.orchestrator.analyze("What's declining in sales?")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.plan.intent, "trend")
        self.assertIn("GROUP BY", result.sql)  # grouped by period, not a single flat number

    def test_fallback_clarification_message_is_friendly_not_internal(self):
        result = self.orchestrator.analyze("What is the total profit margin?")
        self.assertFalse(result.success)
        self.assertNotIn("Could not map", result.error)
        self.assertNotIn("measurable column", result.error)

    # -- round 4: unrecognized filter value bug (found via direct testing) --
    # A question naming a filter value that doesn't actually exist in the data
    # (e.g. "for the Northwest region" when only North/South/East/West exist)
    # used to silently drop the unrecognized filter and return the UNFILTERED
    # total, with nothing telling the user their filter was ignored --
    # answering a materially different question than the one asked, with no
    # indication. Fixed in app/llm/mock_client.py::_detect_unrecognized_filter_reference.

    def test_nonexistent_filter_value_is_refused_not_silently_dropped(self):
        result = self.orchestrator.analyze("What is the total revenue for the Northwest region?")
        self.assertFalse(result.success)
        self.assertIn("Northwest", result.error)
        self.assertIn("region", result.error)
        # must list the REAL known values, not invent any
        self.assertIn("North", result.error)
        self.assertIn("South", result.error)

    def test_real_filter_value_still_works_after_the_fix(self):
        result = self.orchestrator.analyze("What is the total revenue for the North region?")
        self.assertTrue(result.success, result.error)
        self.assertIn("WHERE", result.sql)
        self.assertIn("North", result.sql)

    def test_which_region_question_not_misidentified_as_unrecognized_filter(self):
        # Regression guard: "Which region has the highest revenue?" was
        # initially a false positive of this fix -- "Which" (sentence-initial
        # capitalization) was mistaken for a named filter value.
        result = self.orchestrator.analyze("Which region has the highest revenue?")
        self.assertTrue(result.success, result.error)

    def test_grouped_question_without_specific_filter_still_works(self):
        result = self.orchestrator.analyze("What is the total revenue by region?")
        self.assertTrue(result.success, result.error)
        self.assertIn("GROUP BY", result.sql)


if __name__ == "__main__":
    unittest.main()
