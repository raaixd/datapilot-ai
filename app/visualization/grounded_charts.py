"""
Grounded Chart Type Selection and Validation.

Deterministically validates and selects chart types based on returned DataFrame results:
  - time series (datetime or sequential temporal axis + numeric measure) -> 'line'
  - categorical comparison (discrete dimension + aggregate metric) -> 'bar'
  - proportion / composition (low cardinality <= 7 + positive values) -> 'pie'
  - distribution (single continuous numeric variable) -> 'histogram' / 'bar'
  - relationship (two continuous numeric variables) -> 'scatter'
  - scalar (single value) or raw rows -> 'table' / None

Prevents generation of meaningless or misleading charts.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def validate_and_select_chart(
    recommended_chart: str | None,
    df: pd.DataFrame,
    dimension_col: str | None = None,
    metric_col: str | None = None,
) -> str | None:
    """Validate the planner's recommended chart against actual DataFrame shape and column types.

    Returns the validated chart type ('bar', 'line', 'pie', 'scatter', 'table') or None.
    """
    if df is None or len(df) == 0:
        return None

    # Single scalar / single row: table view is best, no visual chart needed
    if len(df) <= 1 and len(df.columns) <= 2:
        return "table"

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols]

    # Guess or verify metric and dimension columns
    target_metric = (
        metric_col
        if (metric_col in df.columns and pd.api.types.is_numeric_dtype(df[metric_col]))
        else (numeric_cols[0] if numeric_cols else None)
    )
    target_dim = dimension_col if dimension_col in df.columns else (non_numeric_cols[0] if non_numeric_cols else None)

    rec = (recommended_chart or "").lower().strip()

    # 1. Line Chart Validation
    if rec == "line":
        # Requires at least one numeric measure
        if not target_metric:
            return "table"
        # X-axis should be date/time or sequential
        if target_dim:
            dim_series = df[target_dim]
            is_datetime = (
                pd.api.types.is_datetime64_any_dtype(dim_series)
                or any(k in str(target_dim).lower() for k in ("date", "month", "year", "day", "time", "period"))
            )
            if is_datetime:
                return "line"
        # If dimension is purely categorical without temporal order, fall back to bar
        return "bar"

    # 2. Pie Chart Validation
    if rec == "pie":
        # Pie charts are only readable with 2 to 7 categories and strictly positive values
        if not target_metric or not target_dim:
            return "bar" if target_metric else "table"
        distinct_categories = df[target_dim].nunique()
        has_negatives = bool((df[target_metric] < 0).any())
        if 2 <= distinct_categories <= 7 and not has_negatives:
            return "pie"
        # Exceeds 7 categories or has negative values: bar chart is much clearer
        return "bar"

    # 3. Scatter Plot Validation
    if rec == "scatter":
        # Requires at least two numeric columns
        if len(numeric_cols) >= 2:
            return "scatter"
        return "bar" if len(numeric_cols) >= 1 else "table"

    # 4. Bar Chart Validation
    if rec == "bar":
        if target_metric:
            return "bar"
        return "table"

    # 5. Default selection based on data shape
    if len(numeric_cols) >= 1 and len(df.columns) >= 2:
        if target_dim and any(k in str(target_dim).lower() for k in ("date", "month", "year", "period")):
            return "line"
        return "bar"

    return "table"
