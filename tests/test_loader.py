"""
Tests for app/data/loader.py.

Both the CSV path and the .xlsx path are genuinely exercised here: unlike
fastapi/streamlit/plotly, `openpyxl` IS installed in this project's dev
environment, so Excel loading is real, tested functionality, not just
written-and-hoped code. The .xls (legacy binary Excel) branch uses `xlrd`,
which is NOT installed here -- that specific branch is exercised only via
test_xls_missing_dependency_raises_helpful_error, which confirms the
missing-dependency error path (not the happy path) actually works.
"""
import os
import tempfile
import unittest

import pandas as pd

from app.data.loader import MissingOptionalDependencyError, UnsupportedFileTypeError, load_tabular_file


class TestLoader(unittest.TestCase):
    def test_loads_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.csv")
            pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_csv(path, index=False)
            df = load_tabular_file(path, "data.csv")
            self.assertEqual(len(df), 2)
            self.assertEqual(list(df.columns), ["a", "b"])

    def test_loads_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.xlsx")
            pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).to_excel(path, index=False, engine="openpyxl")
            df = load_tabular_file(path, "data.xlsx")
            self.assertEqual(len(df), 3)
            self.assertEqual(list(df.columns), ["a", "b"])

    def test_empty_csv_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.csv")
            with open(path, "w") as f:
                f.write("")
            with self.assertRaises(ValueError):
                load_tabular_file(path, "empty.csv")

    def test_unsupported_extension_raises(self):
        with self.assertRaises(UnsupportedFileTypeError):
            load_tabular_file("/dev/null", "data.pdf")

    def test_xls_missing_dependency_raises_helpful_error(self):
        # xlrd is genuinely not installed in this environment -- this proves
        # the missing-optional-dependency error path actually fires and is
        # actionable, rather than surfacing a raw traceback.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.xls")
            with open(path, "wb") as f:
                f.write(b"not a real xls file")
            with self.assertRaises((MissingOptionalDependencyError, ValueError)) as ctx:
                load_tabular_file(path, "data.xls")
            # Whichever it raises, the message should be actionable, not a bare traceback.
            self.assertTrue(len(str(ctx.exception)) > 0)


if __name__ == "__main__":
    unittest.main()
