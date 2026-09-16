import unittest

import pandas as pd

from app.analytics.metrics import compute_metric_alias, compute_result_metrics


class TestMetricAlias(unittest.TestCase):
    def test_sum_alias(self):
        self.assertEqual(compute_metric_alias("sum", "revenue"), "total_revenue")

    def test_avg_alias(self):
        self.assertEqual(compute_metric_alias("avg", "unit_price"), "average_unit_price")

    def test_count_star_alias(self):
        self.assertEqual(compute_metric_alias("count", "*"), "row_count")

    def test_none_metric_alias(self):
        self.assertEqual(compute_metric_alias("sum", None), "row_count")

    def test_max_alias(self):
        self.assertEqual(compute_metric_alias("max", "revenue"), "maximum_revenue")


class TestMetrics(unittest.TestCase):
    def test_zero_row_aggregate_reports_no_data_explicitly(self):
        """Regression test: SUM()/AVG()/etc. over zero source rows returns
        SQL NULL in exactly one row -- this used to be reported only as
        row_count=1 with no metric_alias key at all, which the insight
        narration then described as a vague 'returned 1 row(s)', reading
        like a successful answer when there was actually no data at all."""
        df = pd.DataFrame({"total_revenue": [None]})
        metrics = compute_result_metrics(df, metric_alias="total_revenue")
        self.assertTrue(metrics["no_data"])
        self.assertIsNone(metrics["total_revenue"])

    def test_single_aggregate_value(self):
        df = pd.DataFrame({"total_revenue": [1234.5]})
        metrics = compute_result_metrics(df, metric_alias="total_revenue")
        self.assertEqual(metrics["total_revenue"], 1234.5)

    def test_metric_alias_not_present_returns_row_counts_only(self):
        # Proves this module never falls back to guessing a column named
        # "value" -- if the alias it's told to look for isn't in the
        # result, it reports only structural counts, nothing invented.
        df = pd.DataFrame({"some_other_column": [1, 2, 3]})
        metrics = compute_result_metrics(df, metric_alias="total_revenue")
        self.assertEqual(metrics["row_count"], 3)
        self.assertNotIn("total_revenue", metrics)

    def test_dimension_breakdown_metrics(self):
        df = pd.DataFrame({"region": ["North", "South", "East"], "total_revenue": [300.0, 500.0, 100.0]})
        metrics = compute_result_metrics(df, metric_alias="total_revenue", dimension_column="region")
        self.assertEqual(metrics["total"], 900.0)
        self.assertAlmostEqual(metrics["average"], 300.0)
        self.assertEqual(metrics["top_entry"], "South")
        self.assertEqual(metrics["top_value"], 500.0)

    def test_trend_direction_increasing(self):
        df = pd.DataFrame({"period": ["2024-01", "2024-02", "2024-03"], "total_revenue": [100.0, 150.0, 200.0]})
        metrics = compute_result_metrics(df, metric_alias="total_revenue")
        self.assertEqual(metrics["trend_direction"], "increasing")
        self.assertAlmostEqual(metrics["period_over_period_change_pct"], 33.33, places=1)

    def test_trend_direction_decreasing(self):
        df = pd.DataFrame({"period": ["2024-01", "2024-02"], "total_revenue": [200.0, 100.0]})
        metrics = compute_result_metrics(df, metric_alias="total_revenue")
        self.assertEqual(metrics["trend_direction"], "decreasing")

    def test_empty_dataframe(self):
        df = pd.DataFrame({"total_revenue": pd.Series(dtype=float)})
        metrics = compute_result_metrics(df, metric_alias="total_revenue")
        self.assertEqual(metrics["row_count"], 0)
        self.assertNotIn("total_revenue", metrics)

    def test_no_value_column_falls_back_to_counts(self):
        df = pd.DataFrame({"order_id": ["A", "B"], "region": ["N", "S"]})
        metrics = compute_result_metrics(df, metric_alias="total_revenue")
        self.assertEqual(metrics["row_count"], 2)
        self.assertEqual(metrics["column_count"], 2)
        self.assertNotIn("total", metrics)

    def test_per_dimension_trend_detects_decline(self):
        df = pd.DataFrame(
            {
                "product": ["Widget", "Widget", "Widget", "Gadget", "Gadget", "Gadget"],
                "period": ["2024-01", "2024-02", "2024-03", "2024-01", "2024-02", "2024-03"],
                "total_revenue": [100.0, 80.0, 50.0, 100.0, 120.0, 150.0],
            }
        )
        metrics = compute_result_metrics(df, metric_alias="total_revenue", dimension_column="product")
        self.assertIn("Widget", metrics["declining_entities"])
        self.assertIn("Gadget", metrics["increasing_entities"])
        self.assertEqual(metrics["entities_analyzed"], 2)


if __name__ == "__main__":
    unittest.main()
