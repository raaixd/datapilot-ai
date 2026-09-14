"""
Data profiling and data-quality validation.

This is a self-contained, reusable module: it takes a pandas DataFrame and
returns a structured DataProfile describing its shape, per-column
statistics, and a list of DataQualityWarning objects. Nothing here depends
on the LLM, the database layer, or the web framework, so it can be unit
tested in isolation and reused anywhere a DataFrame shows up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd


@dataclass
class DataQualityWarning:
    column: str
    issue: str          # short machine-readable code, e.g. "missing_values"
    severity: str        # "info" | "warning" | "critical"
    message: str          # human-readable explanation
    affected_rows: int = 0


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    inferred_type: str  # "numeric" | "categorical" | "datetime" | "boolean" | "text"
    non_null_count: int
    null_count: int
    null_pct: float
    distinct_count: int
    sample_values: list = field(default_factory=list)
    # numeric-only
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    std: float | None = None


@dataclass
class DataProfile:
    row_count: int
    column_count: int
    duplicate_row_count: int
    columns: list[ColumnProfile]
    warnings: list[DataQualityWarning]
    profiled_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def has_critical_warnings(self) -> bool:
        return any(w.severity == "critical" for w in self.warnings)

    def to_dict(self) -> dict:
        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "duplicate_row_count": self.duplicate_row_count,
            "profiled_at": self.profiled_at,
            "columns": [vars(c) for c in self.columns],
            "warnings": [vars(w) for w in self.warnings],
        }


# Categorical columns with more distinct values than this (relative to row
# count) are flagged as "unexpectedly high cardinality" -- often a sign a
# column is really a free-text or identifier field, not a category.
_HIGH_CARDINALITY_RATIO = 0.9
_MIN_ROWS_FOR_CARDINALITY_CHECK = 20


def _is_stringlike(series: pd.Series) -> bool:
    """True for classic object-dtype string columns AND pandas' newer
    dedicated string dtype (the default backing for `str` columns as of
    pandas 3.x), so profiling behaves the same across pandas versions."""
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)


def _infer_semantic_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    # Try to sniff a datetime-like object/string column without forcing a
    # cast on the actual data (profiling must never mutate the input).
    non_null = series.dropna()
    if len(non_null) > 0:
        sample = non_null.head(50)
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            if parsed.notna().mean() > 0.8:
                return "datetime"
        except (ValueError, TypeError):
            pass

    distinct_ratio = series.nunique(dropna=True) / max(len(non_null), 1)
    if distinct_ratio < 0.5 or series.nunique(dropna=True) <= 50:
        return "categorical"
    return "text"


def _check_invalid_numeric_strings(series: pd.Series) -> int:
    """Count values in an object column that look numeric-ish but fail to parse."""
    non_null = series.dropna().astype(str)
    if len(non_null) == 0:
        return 0
    looks_numeric = non_null.str.match(r"^\s*-?\d+[.,]?\d*\s*$")
    return int((~looks_numeric & non_null.str.contains(r"\d")).sum())


def _check_date_parsing(series: pd.Series) -> tuple[int, int]:
    """Return (parseable_count, unparseable_count) for a likely-date column."""
    non_null = series.dropna()
    if len(non_null) == 0:
        return 0, 0
    parsed = pd.to_datetime(non_null, errors="coerce", format="mixed")
    unparseable = int(parsed.isna().sum())
    return int(len(non_null) - unparseable), unparseable


class DataProfiler:
    """Profiles a DataFrame and produces a DataProfile with warnings."""

    def profile(self, df: pd.DataFrame, dataset_name: str = "dataset") -> DataProfile:
        warnings: list[DataQualityWarning] = []
        columns: list[ColumnProfile] = []

        duplicate_count = int(df.duplicated().sum())
        if duplicate_count > 0:
            warnings.append(
                DataQualityWarning(
                    column="*",
                    issue="duplicate_rows",
                    severity="warning" if duplicate_count / max(len(df), 1) < 0.1 else "critical",
                    message=f"{duplicate_count} duplicate row(s) found out of {len(df)}.",
                    affected_rows=duplicate_count,
                )
            )

        if len(df) == 0:
            warnings.append(
                DataQualityWarning(
                    column="*",
                    issue="empty_dataset",
                    severity="critical",
                    message=f"'{dataset_name}' has no rows to analyze.",
                )
            )

        for col in df.columns:
            series = df[col]
            null_count = int(series.isna().sum())
            non_null_count = int(len(series) - null_count)
            null_pct = round((null_count / len(series)) * 100, 2) if len(series) else 0.0
            semantic_type = _infer_semantic_type(series)
            distinct_count = int(series.nunique(dropna=True))

            profile_row = ColumnProfile(
                name=str(col),
                dtype=str(series.dtype),
                inferred_type=semantic_type,
                non_null_count=non_null_count,
                null_count=null_count,
                null_pct=null_pct,
                distinct_count=distinct_count,
                sample_values=[str(v) for v in series.dropna().unique()[:5]],
            )

            if semantic_type == "numeric":
                numeric_series = pd.to_numeric(series, errors="coerce")
                if numeric_series.notna().any():
                    profile_row.min = float(numeric_series.min())
                    profile_row.max = float(numeric_series.max())
                    profile_row.mean = round(float(numeric_series.mean()), 4)
                    profile_row.std = round(float(numeric_series.std() or 0.0), 4)
                    looks_like_money_field = any(w in str(col).lower() for w in ("price", "amount", "revenue", "cost", "total"))
                    if looks_like_money_field and (numeric_series < 0).any():
                        neg_count = int((numeric_series < 0).sum())
                        warnings.append(
                            DataQualityWarning(
                                column=str(col),
                                issue="invalid_numeric_values",
                                severity="warning",
                                message=f"'{col}' contains {neg_count} negative value(s), which is unusual for this kind of field.",
                                affected_rows=neg_count,
                            )
                        )

            if null_count > 0:
                severity = "critical" if null_pct > 50 else ("warning" if null_pct > 5 else "info")
                warnings.append(
                    DataQualityWarning(
                        column=str(col),
                        issue="missing_values",
                        severity=severity,
                        message=f"'{col}' is missing {null_count} value(s) ({null_pct}%).",
                        affected_rows=null_count,
                    )
                )

            if semantic_type == "text" or (_is_stringlike(series) and semantic_type != "datetime"):
                invalid_numeric = _check_invalid_numeric_strings(series)
                if invalid_numeric > 0 and semantic_type != "categorical":
                    warnings.append(
                        DataQualityWarning(
                            column=str(col),
                            issue="invalid_numeric_values",
                            severity="info",
                            message=f"'{col}' has {invalid_numeric} value(s) that mix digits and non-numeric characters.",
                            affected_rows=invalid_numeric,
                        )
                    )

            looks_like_date_name = any(h in str(col).lower() for h in ("date", "time"))
            if _is_stringlike(series) and (semantic_type == "datetime" or looks_like_date_name):
                parseable, unparseable = _check_date_parsing(series)
                if unparseable > 0 and parseable > 0:
                    warnings.append(
                        DataQualityWarning(
                            column=str(col),
                            issue="date_parsing_issue",
                            severity="warning",
                            message=f"'{col}' looks like a date column but {unparseable} value(s) could not be parsed.",
                            affected_rows=unparseable,
                        )
                    )

            if (
                semantic_type == "categorical"
                and len(df) >= _MIN_ROWS_FOR_CARDINALITY_CHECK
                and distinct_count / max(non_null_count, 1) > _HIGH_CARDINALITY_RATIO
            ):
                warnings.append(
                    DataQualityWarning(
                        column=str(col),
                        issue="unexpected_categorical_cardinality",
                        severity="info",
                        message=f"'{col}' has {distinct_count} distinct values across {non_null_count} rows -- almost every row is unique, which is unusual for a category column.",
                        affected_rows=0,
                    )
                )

            columns.append(profile_row)

        return DataProfile(
            row_count=len(df),
            column_count=len(df.columns),
            duplicate_row_count=duplicate_count,
            columns=columns,
            warnings=warnings,
        )
