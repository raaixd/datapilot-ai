"""
Unit tests for grounded chart validation and selection in app/visualization/grounded_charts.py.
"""

import unittest

import pandas as pd

from app.visualization.grounded_charts import validate_and_select_chart


class TestGroundedCharts(unittest.TestCase):
    def test_empty_df_returns_none(self):
        df = pd.DataFrame()
        self.assertIsNone(validate_and_select_chart("bar", df))

    def test_single_scalar_returns_table(self):
        df = pd.DataFrame({"total": [42.0]})
        self.assertEqual(validate_and_select_chart("bar", df), "table")

    def test_line_chart_with_temporal_column_name(self):
        df = pd.DataFrame({
            "order_date": ["2024-01-01", "2024-02-01", "2024-03-01"],
            "revenue": [100.0, 200.0, 300.0],
        })
        self.assertEqual(validate_and_select_chart("line", df, "order_date", "revenue"), "line")

    def test_line_chart_with_datetime_dtype(self):
        df = pd.DataFrame({
            "period": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
            "sales": [150.0, 250.0, 350.0],
        })
        self.assertEqual(validate_and_select_chart("line", df, "period", "sales"), "line")

    def test_line_chart_without_temporal_falls_back_to_bar(self):
        df = pd.DataFrame({
            "category": ["Electronics", "Apparel", "Furniture"],
            "revenue": [100.0, 200.0, 300.0],
        })
        self.assertEqual(validate_and_select_chart("line", df, "category", "revenue"), "bar")

    def test_line_chart_without_numeric_metric_falls_back_to_table(self):
        df = pd.DataFrame({
            "order_date": ["2024-01-01", "2024-02-01"],
            "category": ["Electronics", "Apparel"],
        })
        self.assertEqual(validate_and_select_chart("line", df, "order_date", "category"), "table")

    def test_pie_chart_with_valid_categories(self):
        df = pd.DataFrame({
            "channel": ["Direct", "Organic", "Paid", "Referral"],
            "sessions": [1000, 2500, 800, 300],
        })
        self.assertEqual(validate_and_select_chart("pie", df, "channel", "sessions"), "pie")

    def test_pie_chart_exceeding_7_categories_falls_back_to_bar(self):
        df = pd.DataFrame({
            "country": [f"Country_{i}" for i in range(10)],
            "users": [100 * (i + 1) for i in range(10)],
        })
        self.assertEqual(validate_and_select_chart("pie", df, "country", "users"), "bar")

    def test_pie_chart_with_negative_values_falls_back_to_bar(self):
        df = pd.DataFrame({
            "category": ["A", "B", "C"],
            "net_profit": [100.0, -50.0, 80.0],
        })
        self.assertEqual(validate_and_select_chart("pie", df, "category", "net_profit"), "bar")

    def test_scatter_plot_with_two_numerics(self):
        df = pd.DataFrame({
            "discount_pct": [0.05, 0.10, 0.15, 0.20],
            "profit_margin": [0.25, 0.20, 0.18, 0.12],
        })
        self.assertEqual(validate_and_select_chart("scatter", df), "scatter")

    def test_scatter_plot_with_single_numeric_falls_back_to_bar(self):
        df = pd.DataFrame({
            "category": ["A", "B", "C"],
            "revenue": [100.0, 200.0, 300.0],
        })
        self.assertEqual(validate_and_select_chart("scatter", df), "bar")

    def test_bar_chart_valid(self):
        df = pd.DataFrame({
            "region": ["North", "South", "East", "West"],
            "revenue": [400.0, 300.0, 200.0, 100.0],
        })
        self.assertEqual(validate_and_select_chart("bar", df, "region", "revenue"), "bar")


if __name__ == "__main__":
    unittest.main()
