import json
import unittest

from app.agents.planner import AnalysisPlan, AnalysisPlanner, PlanValidationError
from app.data.database import TableSchema
from app.llm.base import LLMClient


def _base_kwargs(**overrides):
    kwargs = dict(
        intent="aggregation", table="sales", metric_column="revenue", aggregation="sum",
        dimension_column=None, date_column=None, filters=[], chart_type="table",
        clarification_needed=None,
    )
    kwargs.update(overrides)
    return kwargs


class TestPlanValidation(unittest.TestCase):
    def test_valid_plan_passes(self):
        plan = AnalysisPlan(**_base_kwargs())
        plan.validate()  # should not raise

    def test_invalid_intent_rejected(self):
        plan = AnalysisPlan(**_base_kwargs(intent="do_something_bad"))
        with self.assertRaises(PlanValidationError):
            plan.validate()

    def test_invalid_aggregation_rejected(self):
        plan = AnalysisPlan(**_base_kwargs(aggregation="median"))
        with self.assertRaises(PlanValidationError):
            plan.validate()

    def test_invalid_sort_direction_rejected(self):
        plan = AnalysisPlan(**_base_kwargs(sort_direction="sideways"))
        with self.assertRaises(PlanValidationError):
            plan.validate()

    def test_invalid_chart_type_rejected(self):
        plan = AnalysisPlan(**_base_kwargs(chart_type="pyramid"))
        with self.assertRaises(PlanValidationError):
            plan.validate()

    def test_negative_limit_rejected(self):
        plan = AnalysisPlan(**_base_kwargs(limit=-5))
        with self.assertRaises(PlanValidationError):
            plan.validate()

    def test_answerable_plan_without_metric_column_rejected(self):
        plan = AnalysisPlan(**_base_kwargs(metric_column=None))
        with self.assertRaises(PlanValidationError):
            plan.validate()

    def test_profile_only_intent_does_not_require_metric_column(self):
        plan = AnalysisPlan(**_base_kwargs(intent="missing_data", metric_column=None))
        plan.validate()  # should not raise
        self.assertTrue(plan.is_profile_only)

    def test_unsupported_intent_does_not_require_metric_column(self):
        plan = AnalysisPlan(**_base_kwargs(intent="unsupported", table=None, metric_column=None))
        plan.validate()  # should not raise
        self.assertFalse(plan.is_answerable)

    def test_legacy_comparison_intent_is_normalized(self):
        class LegacyResponseClient(LLMClient):
            def complete(self, system_prompt, user_prompt):
                return json.dumps({
                    "intent": "comparison", "table": "sales", "metric_column": "revenue",
                    "aggregation": "sum", "dimension_column": "region", "date_column": None,
                    "filters": [], "chart_type": "bar", "clarification_needed": None,
                })

        schema = {"sales": TableSchema("sales", [("region", "TEXT"), ("revenue", "REAL")], 2)}
        plan = AnalysisPlanner(LegacyResponseClient()).plan("Compare revenue by region", schema)
        self.assertEqual(plan.intent, "grouped_comparison")


if __name__ == "__main__":
    unittest.main()
