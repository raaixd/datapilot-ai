"""
VERIDEX Comprehensive Grounded Analytical Report Generator.

Generates a structured, 10-section publication-ready analytical report grounded in
deterministic dataset profiling statistics, semantic schema inference, and verified
executed SQL query results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.agents.orchestrator import AnalysisResult
from app.data.profiler import DataProfile
from app.data.semantic_schema import SemanticSchema


@dataclass
class AnalyticalReport:
    report_id: str
    project_name: str
    dataset_name: str
    generated_at: str
    sections: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        s = self.sections
        lines = [
            f"# VERIDEX Analytical Report: {self.project_name}",
            f"**Dataset:** {self.dataset_name} | **Generated:** {self.generated_at}",
            "",
            "---",
            "",
            "## 1. Executive Dataset Overview",
            s.get("overview", {}).get("summary", "No dataset overview available."),
            "",
        ]

        # Key stats table
        ov = s.get("overview", {})
        lines.extend([
            f"- **Total Rows:** {ov.get('row_count', 'N/A')}",
            f"- **Total Columns:** {ov.get('column_count', 'N/A')}",
            f"- **Primary Key Candidate:** {ov.get('primary_key', 'None detected')}",
            f"- **Identified Target Variables:** {', '.join(ov.get('target_variables', [])) or 'None'}",
            "",
            "## 2. Data Quality Assessment",
            s.get("data_quality", {}).get("summary", "Data quality checks passed."),
            "",
        ])

        warnings = s.get("data_quality", {}).get("warnings", [])
        if warnings:
            lines.append("### Quality Warnings Flagged:")
            for w in warnings:
                lines.append(f"- **[{w.get('severity', 'INFO').upper()}]** {w.get('message')}")
            lines.append("")

        lines.extend([
            "## 3. Key Descriptive Statistics",
            s.get("descriptive_statistics", {}).get("summary", "Descriptive statistics computed."),
            "",
        ])
        stats = s.get("descriptive_statistics", {}).get("columns", [])
        if stats:
            lines.append("| Column | Type | Mean | Min | Max | Outliers |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for c in stats[:15]:
                lines.append(f"| {c.get('name')} | {c.get('type')} | {c.get('mean')} | {c.get('min')} | {c.get('max')} | {c.get('outliers', 0)} |")
            lines.append("")

        lines.extend([
            "## 4. Notable Trends & Temporal Patterns",
            s.get("trends", {}).get("summary", "No temporal trends detected."),
            "",
            "## 5. Detected Anomalies & Outliers",
            s.get("anomalies", {}).get("summary", "No critical anomalies flagged."),
            "",
            "## 6. Key Analytical Questions Answered",
        ])

        qa_list = s.get("questions_answered", [])
        if qa_list:
            for qa in qa_list:
                status_icon = "✓" if qa.get("success") else "✗"
                lines.append(f"### {status_icon} {qa.get('question')}")
                lines.append(f"*{qa.get('insight')}*")
                lines.append("")
        else:
            lines.append("No specific analytical questions recorded for this session.")
            lines.append("")

        lines.extend([
            "## 7. Strategic Findings",
            s.get("strategic_findings", {}).get("summary", "All findings grounded in executed SQL data."),
            "",
            "## 8. Supporting Grounded Visualizations",
        ])

        charts = s.get("visualizations", [])
        if charts:
            for ch in charts:
                lines.append(f"- **{ch.get('chart_type', 'table').upper()}**: {ch.get('title')} (Dimension: {ch.get('dimension')}, Metric: {ch.get('metric')})")
            lines.append("")
        else:
            lines.append("No visual charts generated.")
            lines.append("")

        lines.extend([
            "## 9. Verified SQL Queries & Audit Trail",
        ])

        queries = s.get("sql_queries", [])
        if queries:
            for q in queries:
                lines.append(f"```sql\n{q.get('sql')}\n```")
                lines.append(f"*Execution Time:* {q.get('execution_time_ms', 0)}ms | *Repairs:* {q.get('retry_count', 0)}")
                lines.append("")
        else:
            lines.append("No SQL queries executed.")
            lines.append("")

        lines.extend([
            "## 10. Analytical Limitations & Governance Caveats",
            s.get("governance", {}).get("summary", "Standard data verification caveats apply."),
            "",
            "---",
            "*Report generated by VERIDEX AI Data Analyst Platform. Deterministic operations were computed deterministically.*",
        ])

        return "\n".join(lines)


def generate_analytical_report(
    dataset_name: str,
    profile: DataProfile | None = None,
    semantic_schema: SemanticSchema | None = None,
    analysis_results: list[AnalysisResult] | None = None,
    project_name: str = "Default Project",
    report_id: str | None = None,
) -> AnalyticalReport:
    """Deterministically compile a 10-section grounded analytical report."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    rid = report_id or f"rep-{int(datetime.now(UTC).timestamp())}"
    results = analysis_results or []

    # 1. Overview
    row_cnt = profile.row_count if profile else (semantic_schema.row_count if semantic_schema else 0)
    col_cnt = profile.column_count if profile else (semantic_schema.column_count if semantic_schema else 0)
    pk = semantic_schema.primary_key_candidate if semantic_schema else None
    targets = semantic_schema.target_variable_candidates if semantic_schema else []
    overview = {
        "row_count": row_cnt,
        "column_count": col_cnt,
        "primary_key": pk,
        "target_variables": targets,
        "summary": f"Dataset '{dataset_name}' contains {row_cnt:,} records across {col_cnt} columns.",
    }

    # 2. Data Quality
    quality_warnings = []
    if profile:
        for w in profile.warnings:
            quality_warnings.append({"severity": w.severity, "message": w.message, "issue": w.issue})
    dq_summary = (
        f"{len(quality_warnings)} quality issue(s) detected during profiling."
        if quality_warnings
        else "No data quality anomalies detected during ingestion profiling."
    )
    data_quality = {"summary": dq_summary, "warnings": quality_warnings}

    # 3. Descriptive Stats
    col_stats = []
    if profile:
        for c in profile.columns:
            if c.mean is not None:
                col_stats.append({
                    "name": c.name,
                    "type": c.inferred_type,
                    "mean": round(c.mean, 2),
                    "min": round(c.min, 2) if c.min is not None else None,
                    "max": round(c.max, 2) if c.max is not None else None,
                    "outliers": c.outlier_count or 0,
                })
    descriptive_statistics = {
        "summary": f"Computed distribution parameters for {len(col_stats)} numeric metric(s).",
        "columns": col_stats,
    }

    # 4. Trends
    trend_findings = []
    for r in results:
        if r.plan and r.plan.intent in ("trend", "trend_by_dimension", "percentage_change") and r.success:
            trend_findings.append(r.insight)
    trends = {
        "summary": " ".join(trend_findings) if trend_findings else "No temporal trends detected in query results.",
        "findings": trend_findings,
    }

    # 5. Anomalies
    anomaly_cols = [c for c in col_stats if c.get("outliers", 0) > 0]
    anomalies = {
        "summary": f"Identified {len(anomaly_cols)} column(s) exhibiting statistical outliers beyond 1.5 IQR bounds."
        if anomaly_cols
        else "No columns exceeded standard outlier thresholds.",
        "columns": [c["name"] for c in anomaly_cols],
    }

    # 6. Questions Answered
    qa_entries = []
    for r in results:
        qa_entries.append({
            "question": r.question,
            "success": r.success,
            "insight": r.insight or r.error,
            "latency_ms": r.total_latency_ms,
        })

    # 7. Strategic Findings
    insights = [r.insight for r in results if r.success and r.insight]
    strategic_findings = {
        "summary": " ".join(insights[:3]) if insights else "Synthesized findings reflect executed query insights.",
        "findings": insights,
    }

    # 8. Visualizations
    vis_entries = []
    for r in results:
        if r.success and r.chart_type and r.chart_type != "table":
            vis_entries.append({
                "title": r.question,
                "chart_type": r.chart_type,
                "dimension": r.plan.dimension_column if r.plan else None,
                "metric": r.plan.metric_column if r.plan else None,
            })

    # 9. SQL Queries
    sql_entries = []
    for r in results:
        if r.sql:
            sql_entries.append({
                "question": r.question,
                "sql": r.sql,
                "execution_time_ms": r.sql_execution_time_ms,
                "retry_count": r.retry_count,
            })

    # 10. Governance & Limitations
    suspicious = semantic_schema.suspicious_columns if semantic_schema else []
    governance = {
        "summary": f"Findings are bounded by the {row_cnt:,} rows sampled in '{dataset_name}'. "
        + (f"Note: suspicious constant/null columns detected: {', '.join(suspicious)}." if suspicious else ""),
        "suspicious_columns": suspicious,
    }

    sections = {
        "overview": overview,
        "data_quality": data_quality,
        "descriptive_statistics": descriptive_statistics,
        "trends": trends,
        "anomalies": anomalies,
        "questions_answered": qa_entries,
        "strategic_findings": strategic_findings,
        "visualizations": vis_entries,
        "sql_queries": sql_entries,
        "governance": governance,
    }

    return AnalyticalReport(
        report_id=rid,
        project_name=project_name,
        dataset_name=dataset_name,
        generated_at=now_str,
        sections=sections,
    )
