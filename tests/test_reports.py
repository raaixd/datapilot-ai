import os
import tempfile
import unittest

from app.agents.orchestrator import AnalysisResult
from app.reports.markdown_report import render_markdown_report
from app.reports.pdf_report import render_pdf_report


def _success_result():
    return AnalysisResult(
        question="What is total revenue by region?",
        success=True,
        sql="SELECT region, SUM(revenue) AS value FROM sales GROUP BY region LIMIT 1000",
        result_preview=[{"region": "North", "value": 400.0}, {"region": "South", "value": 650.0}],
        metrics={"total": 1050.0, "top_entry": "South", "top_value": 650.0},
        insight="Total revenue across regions is 1050.0, led by South at 650.0.",
        chart_type="bar",
        follow_up_questions=["How does this trend over time?"],
    )


def _failure_result():
    return AnalysisResult(question="asdf", success=False, error="The question could not be understood.")


class TestMarkdownReport(unittest.TestCase):
    def test_success_report_contains_key_sections(self):
        report = render_markdown_report(_success_result())
        self.assertIn("# VERIDEX", report)
        self.assertIn("## Executive Summary", report)
        self.assertIn("SELECT region, SUM(revenue)", report)
        self.assertIn("South", report)

    def test_failure_report_shows_error_not_fake_data(self):
        report = render_markdown_report(_failure_result())
        self.assertIn("could not be answered", report)
        self.assertNotIn("## Key Metrics", report)


class TestPDFReport(unittest.TestCase):
    def test_generates_valid_pdf_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.pdf")
            render_pdf_report(_success_result(), path)
            self.assertTrue(os.path.exists(path))
            with open(path, "rb") as f:
                header = f.read(5)
            self.assertEqual(header, b"%PDF-")
            self.assertGreater(os.path.getsize(path), 500)

    def test_failure_result_still_produces_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.pdf")
            render_pdf_report(_failure_result(), path)
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
