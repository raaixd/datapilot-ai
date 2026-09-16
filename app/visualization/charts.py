"""
Chart construction from an AnalysisResult.

NOTE ON TESTING: this module requires the `plotly` package, which is not
installed in the sandbox this project was developed in (no network access
to pip install it). The code below was syntax-checked with
`python -m py_compile` but has NOT been run end-to-end here. Install
plotly and run `pytest tests/` locally, or exercise it via the Streamlit
app, before relying on it.

Round 2 fix: this module used to assume the aggregated column was always
literally named "value". It now reads `result.plan.metric_alias` (the same
name app/analytics/metrics.py uses -- see compute_metric_alias()), so a
query that returns `total_revenue` or `average_unit_price` charts correctly
without any special-casing here.
"""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go

from app.agents.orchestrator import AnalysisResult


def build_chart(result: AnalysisResult, dark_mode: bool = True) -> go.Figure | None:
    """Build the chart type recommended by the analysis plan from the
    result preview rows. Returns None if there is nothing sensible to plot."""
    if not result.success or not result.result_preview:
        return None

    metric_col = (result.plan.metric_alias if result.plan else None) or _guess_metric_column(result.result_preview[0])
    if metric_col is None or metric_col not in result.result_preview[0]:
        return None

    rows = result.result_preview
    columns = list(rows[0].keys())
    label_col = next((c for c in columns if c != metric_col), None)
    if label_col is None:
        return None

    x = [row[label_col] for row in rows]
    y = [row[metric_col] for row in rows]

    chart_type = result.chart_type or "bar"
    axis_labels = {"x": label_col, "y": metric_col.replace("_", " ")}

    if chart_type == "line":
        fig = px.line(x=x, y=y, markers=True, labels=axis_labels)
    elif chart_type == "pie":
        fig = px.pie(names=x, values=y)
    elif chart_type == "scatter":
        fig = px.scatter(x=x, y=y, labels=axis_labels)
    else:  # default: bar
        fig = px.bar(x=x, y=y, labels=axis_labels)

    template = "plotly_dark" if dark_mode else "plotly_white"
    fig.update_layout(
        template=template,
        margin=dict(l=30, r=20, t=30, b=30),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, -apple-system, sans-serif", size=12, color="#94A3B8"),
    )
    if dark_mode:
        fig.update_xaxes(gridcolor="rgba(255, 255, 255, 0.06)", zerolinecolor="rgba(255, 255, 255, 0.08)")
        fig.update_yaxes(gridcolor="rgba(255, 255, 255, 0.06)", zerolinecolor="rgba(255, 255, 255, 0.08)")
    return fig


def _guess_metric_column(sample_row: dict) -> str | None:
    """Fallback for results that didn't come with a plan (e.g. a future
    caller that only has raw rows) -- picks the first numeric-looking
    column that isn't obviously a label."""
    for key, val in sample_row.items():
        if key in ("period",):
            continue
        if isinstance(val, (int, float)):
            return key
    return None
