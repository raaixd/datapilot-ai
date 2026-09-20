"""
Data profiling and data-quality validation.

Calculates deterministic statistics across datasets and columns:
  - General: row count, column count, memory usage, duplicate rows, null rows.
  - Column level: data types, null counts/percentages, distinct counts/percentages,
    min, max, mean, median, standard deviation, quantiles (25%, 50%, 75%).
  - Categorical: top values, frequencies, cardinality ratio.
  - Numeric: zero percentages, negative counts, outlier counts via IQR.
  - Datetime: date ranges (min/max), span in days, parseability checks.
  - Text: average string lengths, empty string percentages.

Zero LLM calls are made here -- all statistics are strictly deterministic
using Pandas and Python standard libraries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd


@dataclass
class DataQualityWarning:
    column: str
    issue: str  # short machine-readable code, e.g. "missing_values"
    severity: str  # "info" | "warning" | "critical"
    message: str  # human-readable explanation
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

    # Numeric statistics
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    std: float | None = None
    median: float | None = None
    q25: float | None = None
    q50: float | None = None
    q75: float | None = None
    zero_count: int = 0
    zero_pct: float = 0.0
    negative_count: int = 0
    negative_pct: float = 0.0
    outlier_count: int = 0

    # Categorical statistics
    top_values: list[str] = field(default_factory=list)
    top_frequencies: dict[str, int] = field(default_factory=dict)
    cardinality_ratio: float = 0.0

    # Datetime statistics
    min_date: str | None = None
    max_date: str | None = None
    date_range_days: int | None = None

    # Text statistics
    avg_text_length: float | None = None
    empty_text_pct: float | None = None
    distinct_pct: float = 0.0


@dataclass
class DataProfile:
    row_count: int
    column_count: int
    duplicate_row_count: int
    columns: list[ColumnProfile]
    warnings: list[DataQualityWarning]
    memory_bytes: int = 0
    memory_mb: float = 0.0
    null_row_count: int = 0
    profiled_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def has_critical_warnings(self) -> bool:
        return any(w.severity == "critical" for w in self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "duplicate_row_count": self.duplicate_row_count,
            "null_row_count": self.null_row_count,
            "memory_bytes": self.memory_bytes,
            "memory_mb": self.memory_mb,
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
    """True for classic object-dtype string columns AND pandas' newer dedicated string dtype."""
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)


def _infer_semantic_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    # Try to sniff a datetime-like object/string column without forcing a cast on the data
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
    parseable = int(parsed.notna().sum())
    unparseable = int(parsed.isna().sum())
    return parseable, unparseable


class DataProfiler:
    """Deterministic DataFrame profiling engine."""

    def profile(self, df: pd.DataFrame, dataset_name: str = "dataset") -> DataProfile:
        warnings: list[DataQualityWarning] = []

        if len(df) == 0:
            warnings.append(
                DataQualityWarning(
                    column="*",
                    issue="empty_dataset",
                    severity="critical",
                    message=f"The dataset '{dataset_name}' contains no rows.",
                    affected_rows=0,
                )
            )
            return DataProfile(
                row_count=0,
                column_count=len(df.columns),
                duplicate_row_count=0,
                columns=[],
                warnings=warnings,
                memory_bytes=0,
                memory_mb=0.0,
                null_row_count=0,
            )

        duplicate_count = int(df.duplicated().sum())
        if duplicate_count > 0:
            warnings.append(
                DataQualityWarning(
                    column="*",
                    issue="duplicate_rows",
                    severity="warning" if duplicate_count > 10 else "info",
                    message=f"Found {duplicate_count} fully duplicate row(s).",
                    affected_rows=duplicate_count,
                )
            )

        # General statistics
        total_rows = len(df)
        total_cols = len(df.columns)
        memory_bytes = int(df.memory_usage(deep=True).sum())
        memory_mb = round(memory_bytes / (1024 * 1024), 3)
        null_row_count = int(df.isna().any(axis=1).sum())

        columns: list[ColumnProfile] = []

        for col in df.columns:
            series = df[col]
            non_null_count = int(series.notna().sum())
            null_count = int(series.isna().sum())
            null_pct = round((null_count / total_rows) * 100, 2)
            distinct_count = int(series.nunique(dropna=True))
            distinct_pct = round((distinct_count / max(non_null_count, 1)) * 100, 2)
            cardinality_ratio = round(distinct_count / max(non_null_count, 1), 4)

            # Up to 5 non-null sample values
            non_null_vals = series.dropna()
            samples = [v.item() if hasattr(v, "item") else v for v in non_null_vals.head(5).tolist()]

            semantic_type = _infer_semantic_type(series)

            profile_row = ColumnProfile(
                name=str(col),
                dtype=str(series.dtype),
                inferred_type=semantic_type,
                non_null_count=non_null_count,
                null_count=null_count,
                null_pct=null_pct,
                distinct_count=distinct_count,
                sample_values=samples,
                cardinality_ratio=cardinality_ratio,
                distinct_pct=distinct_pct,
            )

            # Categorical distributions (top 10 values & frequencies)
            if semantic_type in ("categorical", "boolean", "text") and non_null_count > 0:
                top_counts = series.value_counts(dropna=True).head(10)
                profile_row.top_values = [str(k) for k in top_counts.index.tolist()]
                profile_row.top_frequencies = {str(k): int(v) for k, v in top_counts.items()}

            # Numeric statistics
            if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
                numeric_series = series.dropna()
                if len(numeric_series) > 0:
                    profile_row.min = float(numeric_series.min())
                    profile_row.max = float(numeric_series.max())
                    profile_row.mean = round(float(numeric_series.mean()), 4)
                    profile_row.std = round(float(numeric_series.std() or 0.0), 4)
                    profile_row.median = round(float(numeric_series.median()), 4)

                    # Quantiles (25%, 50%, 75%)
                    q25 = float(numeric_series.quantile(0.25))
                    q50 = float(numeric_series.quantile(0.50))
                    q75 = float(numeric_series.quantile(0.75))
                    profile_row.q25 = round(q25, 4)
                    profile_row.q50 = round(q50, 4)
                    profile_row.q75 = round(q75, 4)

                    # Zero percentage
                    zero_count = int((numeric_series == 0).sum())
                    profile_row.zero_count = zero_count
                    profile_row.zero_pct = round((zero_count / len(numeric_series)) * 100, 2)

                    # Negative values
                    neg_count = int((numeric_series < 0).sum())
                    profile_row.negative_count = neg_count
                    profile_row.negative_pct = round((neg_count / len(numeric_series)) * 100, 2)

                    # Outlier candidates using 1.5 * IQR rule
                    iqr = q75 - q25
                    if iqr > 0:
                        lower_bound = q25 - (1.5 * iqr)
                        upper_bound = q75 + (1.5 * iqr)
                        outliers = ((numeric_series < lower_bound) | (numeric_series > upper_bound)).sum()
                        profile_row.outlier_count = int(outliers)

                    looks_like_money_field = any(
                        w in str(col).lower() for w in ("price", "amount", "revenue", "cost", "total", "sales")
                    )
                    if looks_like_money_field and neg_count > 0:
                        warnings.append(
                            DataQualityWarning(
                                column=str(col),
                                issue="invalid_numeric_values",
                                severity="warning",
                                message=f"'{col}' contains {neg_count} negative value(s), which is unusual for this kind of field.",
                                affected_rows=neg_count,
                            )
                        )

            # Datetime statistics
            if semantic_type == "datetime" and non_null_count > 0:
                try:
                    dt_series = pd.to_datetime(series.dropna(), errors="coerce")
                    valid_dt = dt_series.dropna()
                    if len(valid_dt) > 0:
                        min_dt = valid_dt.min()
                        max_dt = valid_dt.max()
                        profile_row.min_date = str(min_dt)
                        profile_row.max_date = str(max_dt)
                        profile_row.date_range_days = int((max_dt - min_dt).total_seconds() / 86400)
                except Exception:
                    pass

            # Text statistics
            if _is_stringlike(series) and non_null_count > 0:
                str_series = non_null_vals.astype(str)
                str_lens = str_series.str.len()
                profile_row.avg_text_length = round(float(str_lens.mean()), 2)
                empty_strings = int((str_series.str.strip() == "").sum())
                profile_row.empty_text_pct = round((empty_strings / len(str_series)) * 100, 2)

            # Warnings checks
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

            looks_like_date_name = any(h in str(col).lower() for h in ("date", "time", "timestamp"))
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
                and cardinality_ratio > _HIGH_CARDINALITY_RATIO
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
            row_count=total_rows,
            column_count=total_cols,
            duplicate_row_count=duplicate_count,
            columns=columns,
            warnings=warnings,
            memory_bytes=memory_bytes,
            memory_mb=memory_mb,
            null_row_count=null_row_count,
        )
