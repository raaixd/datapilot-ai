"""
Metric computation over an already-executed query result.

Deliberately separate from SQL generation: the SQL query answers "what rows
do I need", and this module answers "what do those rows mean". This split
means metrics can be unit-tested against known DataFrames with known
expected outputs, independent of the LLM and the SQL layer entirely.

IMPORTANT (fixed in round 2): earlier versions of this module assumed the
aggregated column was always literally named "value". That broke the moment
a real LLM (or a improved mock) named its output column something
human-readable like `total_revenue`. Metric identity is now always passed
in explicitly via `metric_alias`, computed by `compute_metric_alias()` from
the same (aggregation, metric_column) pair the SQL generator used -- so the
column name the query actually returns and the column name this module
looks for can never drift apart.
"""
from __future__ import annotations

import math

import pandas as pd

_AGG_LABEL_PREFIX = {
    "sum": "total",
    "avg": "average",
    "min": "minimum",
    "max": "maximum",
    "count": "count",
}


def compute_metric_alias(aggregation: str | None, metric_column: str | None) -> str:
    """The single source of truth for naming an aggregated column, used by
    BOTH the SQL generator (so the query aliases its output this way) and
    this module (so it knows which column to read). Examples:
        ("sum", "revenue")    -> "total_revenue"
        ("avg", "unit_price") -> "average_unit_price"
        ("count", "*")        -> "row_count"
    """
    if metric_column in (None, "*"):
        return "row_count"
    prefix = _AGG_LABEL_PREFIX.get((aggregation or "sum").lower(), (aggregation or "sum").lower())
    return f"{prefix}_{metric_column}"


def compute_result_metrics(
    result: pd.DataFrame,
    metric_alias: str,
    dimension_column: str | None = None,
    period_column: str | None = "period",
) -> dict:
    """Compute a generic metrics dict from a query result.

    `metric_alias` MUST be the actual column name the query produced (see
    compute_metric_alias above) -- this function never guesses. Handles the
    four result shapes the SQL generator produces:
      - a single aggregate value:              columns = [metric_alias]
      - a dimension broken into a metric:       columns = [dimension_column, metric_alias]
      - a time series:                          columns = [period_column, metric_alias]
      - a dimension broken down over time:      columns = [dimension_column, period_column, metric_alias]
    """
    metrics: dict = {"row_count": int(len(result)), "column_count": int(len(result.columns))}

    if result.empty or metric_alias not in result.columns:
        return metrics

    values = pd.to_numeric(result[metric_alias], errors="coerce").dropna()
    has_dimension = dimension_column is not None and dimension_column in result.columns
    has_period = period_column is not None and period_column in result.columns

    if has_dimension and has_period:
        metrics.update(_per_dimension_trend(result, dimension_column, period_column, metric_alias))
        return metrics

    if len(values) == 1:
        # A single row is common for a plain aggregate (no dimension) AND for
        # a ranking query narrowed with LIMIT 1 ("which product has the
        # highest revenue?") -- either way, report the value, and the label
        # it belongs to when there is one.
        metrics[metric_alias] = _clean_number(values.iloc[0])
        label_col = dimension_column if has_dimension else (period_column if has_period else None)
        if label_col is not None:
            metrics["top_entry"] = str(result.iloc[0][label_col])
        return metrics

    if len(values) > 1:
        metrics["total"] = _clean_number(values.sum())
        metrics["average"] = _clean_number(values.mean())
        metrics["min"] = _clean_number(values.min())
        metrics["max"] = _clean_number(values.max())

        label_col = dimension_column if has_dimension else (period_column if has_period else None)
        if label_col is not None:
            top_row = result.loc[values.idxmax()]
            metrics["top_entry"] = str(top_row[label_col])
            metrics["top_value"] = _clean_number(values.max())

            if has_period and not has_dimension:
                metrics["trend_direction"] = _trend_direction(values)
                metrics["period_over_period_change_pct"] = _period_over_period_pct(values)

    return metrics


def _per_dimension_trend(result: pd.DataFrame, dimension_column: str, period_column: str, metric_alias: str) -> dict:
    """For a (dimension, period, metric) result: work out, per dimension
    value, whether that entity's metric increased or decreased between its
    first and last observed period. This is what powers "which products
    experienced declining sales?" -- the SQL only ever needs to group by
    (dimension, period); the actual decline/growth judgment happens here,
    in plain pandas, over a clean already-validated result set."""
    declining, increasing, flat = [], [], []
    for dim_value, group in result.groupby(dimension_column):
        ordered = group.sort_values(period_column)
        series = pd.to_numeric(ordered[metric_alias], errors="coerce").dropna()
        if len(series) < 2:
            continue
        direction = _trend_direction(series)
        (declining if direction == "decreasing" else increasing if direction == "increasing" else flat).append(str(dim_value))

    return {
        "declining_entities": declining,
        "increasing_entities": increasing,
        "flat_entities": flat,
        "entities_analyzed": len(declining) + len(increasing) + len(flat),
    }


def _clean_number(value) -> float | int | None:
    """Coerce numpy/pandas scalar types (int64, float64, ...) to plain
    Python int/float so metrics are always JSON-serializable."""
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return None
    if value.is_integer():
        return int(value)
    return round(value, 4)


def _trend_direction(values: pd.Series) -> str:
    if len(values) < 2:
        return "flat"
    delta = values.iloc[-1] - values.iloc[0]
    if delta > 0:
        return "increasing"
    if delta < 0:
        return "decreasing"
    return "flat"


def _period_over_period_pct(values: pd.Series) -> float | None:
    if len(values) < 2 or values.iloc[-2] == 0:
        return None
    return round(float((values.iloc[-1] - values.iloc[-2]) / values.iloc[-2]) * 100, 2)
