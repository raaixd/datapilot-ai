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
SUPPORTED_ARCHIVE_EXTENSIONS = (".zip",)

# Tried in order for CSV decoding. utf-8-sig handles both plain UTF-8 and a
# leading BOM transparently. cp1252 (Windows-1252) is the most common
# legacy encoding for CSVs exported from older Windows/Excel setups and
# will successfully decode many files utf-8-sig rejects. Deliberately does
# NOT fall all the way to latin-1/ISO-8859-1 as a final catch-all: that
# encoding defines every byte value and therefore never raises a decode
# error, which means genuinely binary/non-text input would "succeed" and
# silently produce garbage rows instead of a clear rejection -- a false
# success is worse here than an honest failure.
_CSV_ENCODING_FALLBACKS = ("utf-8-sig", "cp1252")


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
        return _load_csv_with_encoding_fallback(file_or_path, filename)

    if suffix in (".xlsx", ".xls"):
        engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
        try:
            return pd.read_excel(file_or_path, engine=engine)
        except ImportError as exc:
            raise MissingOptionalDependencyError(
                f"Reading '{suffix}' files requires the '{engine}' package. Install it with `pip install {engine}`."
            ) from exc
        except ValueError as exc:
            # pandas raises ValueError for a corrupt/unrecognized workbook
            raise ValueError(f"'{filename}' could not be read as an Excel file: {exc}") from exc

    raise UnsupportedFileTypeError(
        f"Unsupported file type '{suffix}' for '{filename}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}."
    )


def _load_csv_with_encoding_fallback(file_or_path, filename: str) -> pd.DataFrame:
    last_decode_error: UnicodeDecodeError | None = None
    for attempt, encoding in enumerate(_CSV_ENCODING_FALLBACKS):
        try:
            df = pd.read_csv(file_or_path, encoding=encoding)
            if attempt > 0:
                logger.warning("'%s' was not valid UTF-8; successfully re-read using %s instead.", filename, encoding)
            return df
        except UnicodeDecodeError as exc:
            last_decode_error = exc
            _rewind(file_or_path)
            continue
        except pd.errors.EmptyDataError as exc:
            raise ValueError(f"'{filename}' is empty or not a valid CSV.") from exc
        except pd.errors.ParserError as exc:
            raise ValueError(f"'{filename}' could not be parsed as CSV: {exc}") from exc

    # Every encoding in the fallback chain failed to decode this file --
    # raise a clear, actionable error instead of the last raw UnicodeDecodeError.
    raise ValueError(
        f"'{filename}' could not be read as text (tried {', '.join(_CSV_ENCODING_FALLBACKS)}). "
        f"It may not be a CSV file at all, or it may use an unusual encoding -- "
        f"try re-saving it as UTF-8."
    ) from last_decode_error


def _rewind(file_or_path) -> None:
    """If given a file-like object (as opposed to a path string), reset its
    read position so the next encoding attempt reads from the start again."""
    seek = getattr(file_or_path, "seek", None)
    if callable(seek):
        seek(0)


def load_tabular_archive(file_or_path, filename: str = "archive.zip") -> dict[str, pd.DataFrame]:
    """Load a .zip archive containing one or more CSV or Excel files.

    Returns a dict mapping sanitized table names to loaded DataFrames.
    Skips hidden/system metadata files (like __MACOSX) and subdirectories.
    """
    import io
    import zipfile

    tables: dict[str, pd.DataFrame] = {}
    try:
        with zipfile.ZipFile(file_or_path, "r") as z:
            for member_name in z.namelist():
                # Skip directories and hidden/system metadata files
                base_name = Path(member_name).name
                if member_name.endswith("/") or base_name.startswith((".", "__MACOSX")):
                    continue
                ext = Path(member_name).suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    data = z.read(member_name)
                    table_name = Path(member_name).stem
                    # Clean table name
                    table_name = "".join(ch if ch.isalnum() else "_" for ch in table_name).strip("_")
                    if not table_name:
                        table_name = "dataset"
                    df = load_tabular_file(io.BytesIO(data), member_name)
                    tables[table_name] = df
    except zipfile.BadZipFile as exc:
        raise ValueError(f"'{filename}' is not a valid zip archive.") from exc

    if not tables:
        raise ValueError(
            f"'{filename}' did not contain any valid CSV or Excel files ({', '.join(SUPPORTED_EXTENSIONS)})."
        )
    return tables
