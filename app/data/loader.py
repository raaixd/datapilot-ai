"""
Tabular file loading -- CSV and Excel.

A single entry point (`load_tabular_file`) is used by the CLI demo, the
FastAPI upload endpoint, and the Streamlit uploader, so all three surfaces
handle file types identically and give the same error messages.

NOTE ON TESTING: the CSV path and the .xlsx path (via `openpyxl`, which IS
installed in this project's dev environment) are both exercised by
tests/test_loader.py. `.xls` (old binary Excel format, via `xlrd`) is
written the same way but xlrd was not installed here, so that specific
branch is untested -- see the test file's docstring.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = (".csv", ".xlsx", ".xls")


class UnsupportedFileTypeError(ValueError):
    pass


class MissingOptionalDependencyError(ImportError):
    pass


def load_tabular_file(file_or_path, filename: str) -> pd.DataFrame:
    """Load a CSV or Excel file into a DataFrame.

    `file_or_path` may be a path/str, or any file-like object (e.g. an
    uploaded file's stream) -- pandas accepts both for read_csv/read_excel.
    `filename` is used only to determine the format from its extension, so
    this works the same whether given a real path or an in-memory upload
    that only has a name attached.
    """
    suffix = Path(filename).suffix.lower()

    if suffix == ".csv":
        try:
            return pd.read_csv(file_or_path)
        except pd.errors.EmptyDataError as exc:
            raise ValueError(f"'{filename}' is empty or not a valid CSV.") from exc
        except pd.errors.ParserError as exc:
            raise ValueError(f"'{filename}' could not be parsed as CSV: {exc}") from exc

    if suffix in (".xlsx", ".xls"):
        engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
        try:
            return pd.read_excel(file_or_path, engine=engine)
        except ImportError as exc:
            raise MissingOptionalDependencyError(
                f"Reading '{suffix}' files requires the '{engine}' package. "
                f"Install it with `pip install {engine}`."
            ) from exc
        except ValueError as exc:
            # pandas raises ValueError for a corrupt/unrecognized workbook
            raise ValueError(f"'{filename}' could not be read as an Excel file: {exc}") from exc

    raise UnsupportedFileTypeError(
        f"Unsupported file type '{suffix}' for '{filename}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}."
    )
