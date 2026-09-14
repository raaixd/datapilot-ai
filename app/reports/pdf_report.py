from __future__ import annotations

import io
from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.agents.orchestrator import AnalysisResult


def render_pdf_report(result: AnalysisResult, output_path: str) -> str:
    """Write a PDF report to output_path and return the path. This is
    exercised directly by tests/test_reports.py -- reportlab is a real,
    installed dependency, so this path is actually tested, not just written."""
    with open(output_path, "wb") as output:
        _render_pdf(result, output)
    return output_path


def render_pdf_report_bytes(result: AnalysisResult) -> bytes:
    """Render a PDF in memory so callers need not depend on a temp directory."""
    output = io.BytesIO()
    _render_pdf(result, output)
    return output.getvalue()


def _render_pdf(result: AnalysisResult, output) -> None:
    doc = SimpleDocTemplate(output, pagesize=LETTER)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("DataPilot AI &mdash; Analysis Report", styles["Title"]),
        Paragraph(datetime.now(UTC).strftime("Generated %Y-%m-%d %H:%M UTC"), styles["Normal"]),
        Spacer(1, 0.2 * inch),
        Paragraph("Question", styles["Heading2"]),
        Paragraph(_escape(result.question), styles["Normal"]),
        Spacer(1, 0.15 * inch),
    ]

    if not result.success:
        story.append(Paragraph("Status", styles["Heading2"]))
        story.append(Paragraph(f"This question could not be answered. {_escape(result.error or '')}", styles["Normal"]))
        doc.build(story)
        return

    story.append(Paragraph("Executive Summary", styles["Heading2"]))
    story.append(Paragraph(_escape(result.insight or ""), styles["Normal"]))
    story.append(Spacer(1, 0.15 * inch))

    if result.metrics:
        story.append(Paragraph("Key Metrics", styles["Heading2"]))
        metric_rows = [["Metric", "Value"]] + [
            [k.replace("_", " ").title(), str(v)] for k, v in result.metrics.items()
        ]
        story.append(_styled_table(metric_rows))
        story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("SQL Query", styles["Heading2"]))
    story.append(Paragraph(f"<font face='Courier'>{_escape(result.sql or '')}</font>", styles["Normal"]))
    story.append(Spacer(1, 0.15 * inch))

    if result.result_preview:
        story.append(Paragraph("Result Preview", styles["Heading2"]))
        cols = list(result.result_preview[0].keys())
        rows = [cols] + [[str(row[c]) for c in cols] for row in result.result_preview[:10]]
        story.append(_styled_table(rows))
        story.append(Spacer(1, 0.15 * inch))

    if result.data_quality_warnings:
        story.append(Paragraph("Data Quality Warnings", styles["Heading2"]))
        for w in result.data_quality_warnings:
            story.append(Paragraph(f"[{w.severity.upper()}] {_escape(w.message)}", styles["Normal"]))

    doc.build(story)


def _styled_table(rows: list[list[str]]) -> Table:
    table = Table(rows, hAlign="LEFT")
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2933")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f2f5")]),
        ])
    )
    return table


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
