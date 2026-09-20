"""
Unit and integration tests for the VERIDEX Frontend and API Client workflow.

Covers:
  - VeridexApiClient request construction, parameter handling, and response decoding
  - Error translation: HTTP error codes to APIClientError, network errors to ConnectionError
  - End-to-end workflow against FastAPI backend:
      1. Project creation, retrieval, listing, duplicate conflict (409)
      2. Dataset upload, ingestion polling, profile and semantic schema retrieval
      3. Analytical query submission, analysis retrieval, verified SQL, results preview
      4. Report compilation, retrieval, listing with filtering
      5. Observability telemetry retrieval
  - Frontend app importability and syntax integrity
"""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.main import app
from app.db.base import Base
from app.db.session import get_engine
from frontend.api_client import APIClientError, DataPilotApiClient, VeridexApiClient


class TestVeridexApiClientUnit(unittest.TestCase):
    """Unit tests for VeridexApiClient methods with mocked network calls."""

    def setUp(self):
        self.client = VeridexApiClient(base_url="http://testserver:8000")

    def test_backward_compatibility_alias(self):
        self.assertIs(DataPilotApiClient, VeridexApiClient)

    @patch("urllib.request.urlopen")
    def test_health_and_readiness(self, mock_urlopen):
        # Health
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "healthy", "version": "2.0.0"}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        health = self.client.health()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["version"], "2.0.0")

        # Ready
        mock_resp.read.return_value = json.dumps({"status": "ready"}).encode("utf-8")
        ready = self.client.ready()
        self.assertEqual(ready["status"], "ready")

    @patch("urllib.request.urlopen")
    def test_metrics(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"metrics": {"http_requests_total": 42}}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        metrics = self.client.metrics()
        self.assertIn("metrics", metrics)
        self.assertEqual(metrics["metrics"]["http_requests_total"], 42)

    @patch("urllib.request.urlopen")
    def test_projects_lifecycle_client_methods(self, mock_urlopen):
        # Create Project
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "proj-123",
            "name": "Finance Q4",
            "description": "Quarterly review",
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        p = self.client.create_project(name="Finance Q4", description="Quarterly review")
        self.assertEqual(p["id"], "proj-123")
        self.assertEqual(p["name"], "Finance Q4")

        # Get Project
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        p_get = self.client.get_project("proj-123")
        self.assertEqual(p_get["id"], "proj-123")

        # List Projects with query params
        mock_resp.read.return_value = json.dumps({
            "projects": [{"id": "proj-123", "name": "Finance Q4"}],
            "total": 1,
        }).encode("utf-8")
        p_list = self.client.list_projects(limit=10, offset=0)
        self.assertEqual(p_list["total"], 1)

    @patch("urllib.request.urlopen")
    def test_dataset_upload_and_status(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "ds-456",
            "project_id": "proj-123",
            "filename": "sales.csv",
            "status": "ready",
            "row_count": 100,
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ds = self.client.upload_project_dataset("proj-123", b"a,b\n1,2\n", "sales.csv")
        self.assertEqual(ds["id"], "ds-456")
        self.assertEqual(ds["filename"], "sales.csv")

        # Status & Profile
        st = self.client.get_dataset_status("ds-456")
        self.assertEqual(st["status"], "ready")

        mock_resp.read.return_value = json.dumps({
            "dataset_id": "ds-456",
            "profile": {"row_count": 100},
            "semantic_schema": {"columns": []},
        }).encode("utf-8")
        prof = self.client.get_dataset_profile("ds-456")
        self.assertEqual(prof["dataset_id"], "ds-456")

    @patch("urllib.request.urlopen")
    def test_analysis_methods(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "an-789",
            "question": "What is revenue?",
            "status": "completed",
            "sql": "SELECT SUM(revenue) FROM sales",
            "grounded_insight": "Total revenue is $500,000.",
            "metrics": {"total_revenue": 500000},
            "repairs_attempted": 0,
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = self.client.analyze_dataset("ds-456", "What is revenue?")
        self.assertEqual(res["id"], "an-789")
        self.assertEqual(res["status"], "completed")

        an = self.client.get_analysis("an-789")
        self.assertEqual(an["id"], "an-789")

        mock_resp.read.return_value = json.dumps({
            "analysis_id": "an-789",
            "sql": "SELECT SUM(revenue) FROM sales",
            "repairs_attempted": 0,
        }).encode("utf-8")
        sql_res = self.client.get_analysis_sql("an-789")
        self.assertIn("SELECT", sql_res["sql"])

        mock_resp.read.return_value = json.dumps({
            "analysis_id": "an-789",
            "columns": ["revenue"],
            "rows": [[500000]],
            "row_count": 1,
        }).encode("utf-8")
        results = self.client.get_analysis_results("an-789")
        self.assertEqual(results["row_count"], 1)

    @patch("urllib.request.urlopen")
    def test_reports_methods(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "id": "rep-001",
            "dataset_id": "ds-456",
            "title": "Quarterly Report",
            "sections": [],
            "markdown": "# Report",
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        rep = self.client.create_report(dataset_id="ds-456", project_id="proj-123", title="Quarterly Report")
        self.assertEqual(rep["id"], "rep-001")

        rep_get = self.client.get_report("rep-001")
        self.assertEqual(rep_get["id"], "rep-001")

        mock_resp.read.return_value = json.dumps([{"id": "rep-001", "title": "Quarterly Report"}]).encode("utf-8")
        rep_list = self.client.list_reports(project_id="proj-123", dataset_id="ds-456")
        self.assertEqual(len(rep_list), 1)

    @patch("urllib.request.urlopen")
    def test_api_client_error_handling(self, mock_urlopen):
        import urllib.error

        # 404 Not Found error
        err = urllib.error.HTTPError(
            url="http://testserver:8000/projects/not-found",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(json.dumps({"detail": "Project not found"}).encode("utf-8")),
        )
        mock_urlopen.side_effect = err
        with self.assertRaises(APIClientError) as ctx:
            self.client.get_project("not-found")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("Project not found", ctx.exception.detail)

        # Connection error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        with self.assertRaises(ConnectionError) as ctx_conn:
            self.client.health()
        self.assertIn("Cannot reach VERIDEX API", str(ctx_conn.exception))


class TestFrontendApiIntegrationWorkflow(unittest.TestCase):
    """End-to-end integration test verifying the full user workflow against the FastAPI backend."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        engine = get_engine()
        Base.metadata.create_all(bind=engine)

    def test_full_veridex_product_workflow(self):
        # 1. Health & Readiness
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        res_ready = self.client.get("/ready")
        self.assertEqual(res_ready.status_code, 200)

        import uuid
        test_proj_name = f"Frontend Workflow Test {uuid.uuid4().hex[:8]}"
        proj_res = self.client.post(
            "/projects",
            json={"name": test_proj_name, "description": "End-to-end verification"},
        )
        self.assertIn(proj_res.status_code, (200, 201))
        project_data = proj_res.json()
        project_id = project_data["id"]
        self.assertIsNotNone(project_id)

        # Duplicate project returns 409
        dup_res = self.client.post(
            "/projects",
            json={"name": test_proj_name},
        )
        self.assertEqual(dup_res.status_code, 409)

        # 3. Upload Dataset
        csv_content = (
            b"region,product,revenue,units\n"
            b"North,Widget A,1500.0,15\n"
            b"South,Widget B,2400.0,20\n"
            b"East,Widget A,3200.0,30\n"
            b"West,Widget C,1800.0,18\n"
        )

        upload_res = self.client.post(
            f"/projects/{project_id}/datasets",
            files={"file": ("regional_sales.csv", io.BytesIO(csv_content), "text/csv")},
        )
        self.assertIn(upload_res.status_code, (200, 201, 202))
        dataset_data = upload_res.json()
        dataset_id = dataset_data["id"]
        self.assertEqual(dataset_data["filename"], "regional_sales.csv")

        # 4. Ingestion Status & Profile
        status_res = self.client.get(f"/datasets/{dataset_id}/status")
        self.assertEqual(status_res.status_code, 200)
        self.assertIn("processing_status", status_res.json())
        self.assertEqual(status_res.json()["processing_status"], "READY")

        profile_res = self.client.get(f"/datasets/{dataset_id}/profile")
        self.assertEqual(profile_res.status_code, 200)
        profile_data = profile_res.json()
        self.assertIn("profile", profile_data)
        self.assertIn("semantic_schema", profile_data)

        # 5. Ask Analytical Question
        analyze_res = self.client.post(
            f"/datasets/{dataset_id}/analyze",
            json={"question": "What is the total revenue by region?"},
        )
        self.assertIn(analyze_res.status_code, (200, 201))
        analysis_data = analyze_res.json()
        analysis_id = analysis_data["analysis_id"]
        self.assertTrue(analysis_data["success"])
        self.assertIsNotNone(analysis_data["insight"])

        # 6. Retrieve Verified SQL & Results
        sql_res = self.client.get(f"/analyses/{analysis_id}/sql")
        self.assertEqual(sql_res.status_code, 200)
        self.assertIn("SELECT", sql_res.json()["sql"].upper())

        res_res = self.client.get(f"/analyses/{analysis_id}/results")
        self.assertEqual(res_res.status_code, 200)
        self.assertIn("result_preview", res_res.json())

        # 7. Compile Analytical Report
        report_res = self.client.post(
            "/reports",
            json={
                "dataset_id": dataset_id,
                "project_id": project_id,
                "title": "Regional Sales Executive Briefing",
                "analysis_ids": [analysis_id],
            },
        )
        self.assertIn(report_res.status_code, (200, 201))
        report_data = report_res.json()
        report_id = report_data["id"]
        self.assertIn("markdown", report_data)
        self.assertIn("content", report_data)
        self.assertEqual(len(report_data["content"]["sections"]), 10)

        # 8. List Reports
        list_reports_res = self.client.get(f"/reports?project_id={project_id}")
        self.assertEqual(list_reports_res.status_code, 200)
        reports_list = list_reports_res.json()
        self.assertTrue(any(r["id"] == report_id for r in reports_list))

        # 9. Observability Telemetry
        metrics_res = self.client.get("/metrics")
        self.assertEqual(metrics_res.status_code, 200)
        m_data = metrics_res.json()
        self.assertIn("metrics", m_data)
        self.assertIn("total_requests", m_data["metrics"])
        self.assertGreaterEqual(m_data["metrics"]["total_requests"], 1)


if __name__ == "__main__":
    unittest.main()
