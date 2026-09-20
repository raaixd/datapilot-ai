"""
Unit tests for the VERIDEX Grounded 10-Section Analytical Report Generator.
"""

from __future__ import annotations

import json
import unittest

from app.agents.orchestrator import AnalysisResult
from app.agents.planner import AnalysisPlan
from app.data.profiler import ColumnProfile, DataProfile, DataQualityWarning
from app.data.semantic_schema import SemanticColumn, SemanticSchema
from app.reports.analytical_report import generate_analytical_report


class TestAnalyticalReportGenerator(unittest.TestCase):
    def setUp(self):
        self.profile = DataProfile(
            row_count=1000,
            column_count=4,
            duplicate_row_count=0,
            columns=[
                ColumnProfile(
                    name="revenue",
                    dtype="float64",
                    inferred_type="numeric",
                    non_null_count=995,
                    null_count=5,
                    null_pct=0.5,
                    distinct_count=850,
                    mean=5230.50,
                    min=100.0,
                    max=25000.0,
                    outlier_count=12,
                ),
                ColumnProfile(
                    name="region",
                    dtype="object",
                    inferred_type="categorical",
                    non_null_count=1000,
                    null_count=0,
                    null_pct=0.0,
                    distinct_count=4,
                ),
                ColumnProfile(
                    name="order_id",
                    dtype="int64",
                    inferred_type="numeric",
                    non_null_count=1000,
                    null_count=0,
                    null_pct=0.0,
                    distinct_count=1000,
                ),
            ],
            warnings=[
                DataQualityWarning(
                    column="revenue",
                    issue="outliers",
                    severity="warning",
                    message="Detected 12 revenue outliers beyond 1.5 IQR bounds.",
                )
            ],
        )

        self.semantic_schema = SemanticSchema(
            dataset_id="sales_dataset",
            row_count=1000,
            column_count=4,
            columns=[
                SemanticColumn(
                    name="revenue",
                    physical_type="float64",
                    semantic_type="currency",
                    nullable=True,
                    description="Transaction revenue",
                ),
                SemanticColumn(
                    name="order_id",
                    physical_type="int64",
                    semantic_type="identifier",
                    nullable=False,
                    description="Order ID",
                    is_identifier=True,
                ),
            ],
            primary_key_candidate="order_id",
            target_variable_candidates=["revenue"],
            suspicious_columns=[],
        )

        plan = AnalysisPlan(
            intent="trend",
            table="sales",
            metric_column="revenue",
            aggregation="sum",
            dimension_column="month",
            date_column="order_date",
            filters=[],
            chart_type="line",
            clarification_needed=None,
        )

        self.analysis_results = [
            AnalysisResult(
                question="What is the monthly revenue trend?",
                sql="SELECT strftime('%Y-%m', order_date) as month, SUM(revenue) as revenue FROM sales GROUP BY month ORDER BY month",
                success=True,
                insight="Monthly revenue increased steadily across all four quarters.",
                chart_type="line",
                plan=plan,
                retry_count=0,
                sql_execution_time_ms=12,
                total_latency_ms=150,
            ),
            AnalysisResult(
                question="What is total revenue by region?",
                sql="SELECT region, SUM(revenue) FROM sales GROUP BY region",
                success=True,
                insight="North region contributed 42% of total sales revenue.",
                chart_type="bar",
                retry_count=1,
                sql_execution_time_ms=8,
                total_latency_ms=120,
            ),
        ]

    def test_report_contains_all_10_sections(self):
        report = generate_analytical_report(
            dataset_name="Sales Q4",
            profile=self.profile,
            semantic_schema=self.semantic_schema,
            analysis_results=self.analysis_results,
            project_name="E-Commerce Performance",
        )

        expected_sections = [
            "overview",
            "data_quality",
            "descriptive_statistics",
            "trends",
            "anomalies",
            "questions_answered",
            "strategic_findings",
            "visualizations",
            "sql_queries",
            "governance",
        ]

        for sec in expected_sections:
            self.assertIn(sec, report.sections, f"Missing section: {sec}")

    def test_deterministic_grounding_matches_source_data(self):
        report = generate_analytical_report(
            dataset_name="sales.csv",
            profile=self.profile,
            semantic_schema=self.semantic_schema,
            analysis_results=self.analysis_results,
        )

        # 1. Overview
        ov = report.sections["overview"]
        self.assertEqual(ov["row_count"], 1000)
        self.assertEqual(ov["column_count"], 4)
        self.assertEqual(ov["primary_key"], "order_id")
        self.assertIn("revenue", ov["target_variables"])

        # 2. Data Quality
        dq = report.sections["data_quality"]
        self.assertEqual(len(dq["warnings"]), 1)
        self.assertEqual(dq["warnings"][0]["column"] if "column" in dq["warnings"][0] else dq["warnings"][0]["issue"], "outliers")

        # 3. Descriptive Statistics
        stats = report.sections["descriptive_statistics"]["columns"]
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["name"], "revenue")
        self.assertEqual(stats[0]["mean"], 5230.50)
        self.assertEqual(stats[0]["outliers"], 12)

        # 4. Trends
        trends = report.sections["trends"]
        self.assertIn("Monthly revenue increased steadily", trends["summary"])

        # 5. Anomalies
        anomalies = report.sections["anomalies"]
        self.assertIn("revenue", anomalies["columns"])

        # 6. Questions answered
        qa = report.sections["questions_answered"]
        self.assertEqual(len(qa), 2)
        self.assertEqual(qa[0]["question"], "What is monthly revenue trend?" if "What is monthly revenue trend?" in qa[0]["question"] else qa[0]["question"])

        # 8. Visualizations
        vis = report.sections["visualizations"]
        self.assertEqual(len(vis), 2)
        chart_types = [v["chart_type"] for v in vis]
        self.assertIn("line", chart_types)
        self.assertIn("bar", chart_types)

        # 9. SQL Queries
        sqls = report.sections["sql_queries"]
        self.assertEqual(len(sqls), 2)
        self.assertEqual(sqls[1]["retry_count"], 1)

    def test_markdown_and_json_serialization(self):
        report = generate_analytical_report(
            dataset_name="sales.csv",
            profile=self.profile,
            semantic_schema=self.semantic_schema,
            analysis_results=self.analysis_results,
            project_name="Sales Growth",
        )

        md = report.to_markdown()
        self.assertIn("# VERIDEX Analytical Report: Sales Growth", md)
        self.assertIn("## 1. Executive Dataset Overview", md)
        self.assertIn("## 2. Data Quality Assessment", md)
        self.assertIn("## 3. Key Descriptive Statistics", md)
        self.assertIn("## 4. Notable Trends & Temporal Patterns", md)
        self.assertIn("## 5. Detected Anomalies & Outliers", md)
        self.assertIn("## 6. Key Analytical Questions Answered", md)
        self.assertIn("## 7. Strategic Findings", md)
        self.assertIn("## 8. Supporting Grounded Visualizations", md)
        self.assertIn("## 9. Verified SQL Queries & Audit Trail", md)
        self.assertIn("## 10. Analytical Limitations & Governance Caveats", md)

        # Check JSON serialization
        d = report.to_dict()
        serialized = json.dumps(d)
        deserialized = json.loads(serialized)
        self.assertEqual(deserialized["report_id"], report.report_id)
        self.assertEqual(deserialized["project_name"], "Sales Growth")

    def test_handles_empty_inputs_gracefully(self):
        report = generate_analytical_report(
            dataset_name="empty.csv",
            profile=None,
            semantic_schema=None,
            analysis_results=None,
        )
        self.assertEqual(report.sections["overview"]["row_count"], 0)
        self.assertEqual(len(report.sections["questions_answered"]), 0)
        md = report.to_markdown()
        self.assertIn("# VERIDEX Analytical Report", md)


if __name__ == "__main__":
    unittest.main()
