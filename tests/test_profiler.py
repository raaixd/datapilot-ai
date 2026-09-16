import unittest

import pandas as pd

from app.data.profiler import DataProfiler


class TestDataProfiler(unittest.TestCase):
    def setUp(self):
        self.profiler = DataProfiler()

    def test_basic_shape(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        profile = self.profiler.profile(df)
        self.assertEqual(profile.row_count, 3)
        self.assertEqual(profile.column_count, 2)
        self.assertEqual(profile.duplicate_row_count, 0)

    def test_detects_missing_values(self):
        df = pd.DataFrame({"a": [1, None, 3, None]})
        profile = self.profiler.profile(df)
        missing_warnings = [w for w in profile.warnings if w.issue == "missing_values"]
        self.assertEqual(len(missing_warnings), 1)
        self.assertEqual(missing_warnings[0].affected_rows, 2)

    def test_detects_duplicate_rows(self):
        df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
        profile = self.profiler.profile(df)
        dup_warnings = [w for w in profile.warnings if w.issue == "duplicate_rows"]
        self.assertEqual(len(dup_warnings), 1)
        self.assertEqual(dup_warnings[0].affected_rows, 1)

    def test_empty_dataset_flagged_critical(self):
        df = pd.DataFrame({"a": pd.Series(dtype=int)})
        profile = self.profiler.profile(df, dataset_name="empty.csv")
        self.assertTrue(profile.has_critical_warnings)
        self.assertTrue(any(w.issue == "empty_dataset" for w in profile.warnings))

    def test_numeric_column_statistics(self):
        df = pd.DataFrame({"revenue": [10.0, 20.0, 30.0]})
        profile = self.profiler.profile(df)
        col = next(c for c in profile.columns if c.name == "revenue")
        self.assertEqual(col.inferred_type, "numeric")
        self.assertEqual(col.min, 10.0)
        self.assertEqual(col.max, 30.0)
        self.assertEqual(col.mean, 20.0)

    def test_date_parsing_issue_detected(self):
        df = pd.DataFrame({"order_date": ["2024-01-01", "2024-02-01", "not-a-date", "2024-03-01"]})
        profile = self.profiler.profile(df)
        date_warnings = [w for w in profile.warnings if w.issue == "date_parsing_issue"]
        self.assertEqual(len(date_warnings), 1)
        self.assertEqual(date_warnings[0].affected_rows, 1)

    def test_categorical_column_not_flagged_for_missing_when_complete(self):
        df = pd.DataFrame({"region": ["N", "S", "E", "W"] * 5})
        profile = self.profiler.profile(df)
        col = next(c for c in profile.columns if c.name == "region")
        self.assertEqual(col.inferred_type, "categorical")
        self.assertEqual(col.null_count, 0)
        self.assertFalse(any(w.column == "region" for w in profile.warnings))

    def test_negative_value_warning_only_fires_with_actual_negatives(self):
        # Regression test: an operator-precedence bug used to make this warning
        # fire for ANY column with "amount" in its name, even with zero
        # negative values, because `A.any() and "price" in x or "amount" in x`
        # parsed as `(A.any() and "price" in x) or ("amount" in x)`.
        df = pd.DataFrame({"sales_amount": [10.0, 20.0, 30.0]})
        profile = self.profiler.profile(df)
        self.assertFalse(any(w.issue == "invalid_numeric_values" for w in profile.warnings))

    def test_negative_value_warning_fires_when_negatives_present(self):
        df = pd.DataFrame({"sales_amount": [10.0, -5.0, 30.0]})
        profile = self.profiler.profile(df)
        neg_warnings = [
            w for w in profile.warnings if w.issue == "invalid_numeric_values" and w.column == "sales_amount"
        ]
        self.assertEqual(len(neg_warnings), 1)
        self.assertEqual(neg_warnings[0].affected_rows, 1)

    def test_to_dict_is_serializable(self):
        import json

        df = pd.DataFrame({"a": [1, 2], "b": ["x", None]})
        profile = self.profiler.profile(df)
        json.dumps(profile.to_dict())  # should not raise


if __name__ == "__main__":
    unittest.main()
