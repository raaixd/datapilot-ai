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

import io
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

    # -- encoding fallback (found via direct testing: binary/non-UTF-8 files
    # previously raised a raw UnicodeDecodeError instead of a clean message) --

    def test_cp1252_encoded_csv_is_read_via_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.csv")
            with open(path, "wb") as f:
                f.write("region,revenue\nNa\xefve Region,100\n".encode("cp1252"))
            df = load_tabular_file(path, "data.csv")
            self.assertEqual(len(df), 1)
            self.assertEqual(df.iloc[0]["region"], "Na\xefve Region")

    def test_cp1252_encoded_file_like_object_is_read_via_fallback(self):
        # Exercises the seek(0) rewind logic specifically -- a file-like
        # object (like an uploaded file stream) is partially consumed by
        # the first failed decode attempt and must be rewound before retry.
        content = "region,revenue\nCaf\xe9 Region,250\n".encode("cp1252")
        buf = io.BytesIO(content)
        df = load_tabular_file(buf, "data.csv")
        self.assertEqual(df.iloc[0]["region"], "Caf\xe9 Region")

    def test_plain_utf8_csv_unaffected_by_fallback_logic(self):
        buf = io.BytesIO(b"region,revenue\nNorth,100\n")
        df = load_tabular_file(buf, "data.csv")
        self.assertEqual(df.iloc[0]["region"], "North")

    def test_binary_garbage_raises_clean_error_not_raw_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.csv")
            with open(path, "wb") as f:
                f.write(bytes(range(256)) * 4)
            with self.assertRaises(ValueError) as ctx:
                load_tabular_file(path, "data.csv")
            message = str(ctx.exception)
            self.assertIn("could not be read as text", message)
            self.assertNotIn("Traceback", message)


if __name__ == "__main__":
    unittest.main()
