"""
Integration and functional tests for the VERIDEX REST API Layer.

Covers:
  - System endpoints: /health, /ready, /metrics (and /api/v1/* aliases)
  - Projects endpoints: POST/GET /projects, GET /projects/{id}
  - Datasets endpoints: POST /projects/{id}/datasets, GET /datasets/{id}, /status, /profile
  - Analyses endpoints: POST /datasets/{id}/analyze, GET /analyses/{id}, /sql, /results
  - Reports endpoints: POST /reports, GET /reports/{id}, GET /reports
  - Observability middleware: X-Request-ID propagation, request timing
  - Legacy routes backward compatibility: /upload, /query, /tables/{session_id}
"""

from __future__ import annotations

import io
import unittest

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.observability import get_metrics_collector
from app.db.base import Base
from app.db.session import get_engine


class TestApiV1Endpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        # Ensure database tables exist
        engine = get_engine()
        Base.metadata.create_all(bind=engine)

    def setUp(self):
        self.collector = get_metrics_collector()

    def test_health_endpoint(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["version"], "2.0.0")
        self.assertIn("timestamp", data)
        self.assertIn("llm_provider", data)
        self.assertIn("database_backend", data)
        self.assertIn("active_sessions", data)

        # Verify X-Request-ID header
        self.assertIn("x-request-id", res.headers)

        # Test versioned alias
        v1_res = self.client.get("/api/v1/health")
        self.assertEqual(v1_res.status_code, 200)
        self.assertEqual(v1_res.json()["status"], "healthy")

    def test_readiness_endpoint(self):
        res = self.client.get("/ready")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["database"]["status"], "connected")
        self.assertEqual(data["storage"]["status"], "connected")

        # Test versioned alias
        v1_res = self.client.get("/api/v1/ready")
        self.assertEqual(v1_res.status_code, 200)
        self.assertEqual(v1_res.json()["status"], "ready")

    def test_metrics_endpoint(self):
        res = self.client.get("/metrics")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("metrics", data)
        metrics = data["metrics"]
        self.assertIn("uptime_seconds", metrics)
        self.assertIn("total_requests", metrics)
        self.assertIn("latency_ms", metrics)
        self.assertIn("p50", metrics["latency_ms"])

        # Test versioned alias
        v1_res = self.client.get("/api/v1/metrics")
        self.assertEqual(v1_res.status_code, 200)

    def test_request_id_middleware_propagation(self):
        custom_req_id = "custom-uuid-test-999"
        res = self.client.get("/health", headers={"X-Request-ID": custom_req_id})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("x-request-id"), custom_req_id)

    def test_projects_lifecycle(self):
        import uuid

        proj_name = f"Test Project {uuid.uuid4().hex[:6]}"
        create_res = self.client.post(
            "/projects",
            json={"name": proj_name, "description": "A sample portfolio project"},
        )
        self.assertEqual(create_res.status_code, 201)
        proj = create_res.json()
        self.assertEqual(proj["name"], proj_name)
        proj_id = proj["id"]

        # Duplicate project returns 409
        dup_res = self.client.post(
            "/projects",
            json={"name": proj_name, "description": "Duplicate name"},
        )
        self.assertEqual(dup_res.status_code, 409)

        # Get project by ID
        get_res = self.client.get(f"/projects/{proj_id}")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["id"], proj_id)

        # List projects
        list_res = self.client.get("/projects")
        self.assertEqual(list_res.status_code, 200)
        self.assertGreaterEqual(list_res.json()["total"], 1)

        # Non-existent project
        missing_res = self.client.get("/projects/non-existent-proj-id")
        self.assertEqual(missing_res.status_code, 404)

    def test_datasets_and_analysis_and_reports_flow(self):
        import uuid

        # 1. Create a project
        proj_name = f"Analytics Proj {uuid.uuid4().hex[:6]}"
        proj_res = self.client.post("/projects", json={"name": proj_name})
        self.assertEqual(proj_res.status_code, 201)
        project_id = proj_res.json()["id"]

        # 2. Upload and ingest a dataset
        csv_content = (
            b"order_id,region,revenue,units\n"
            b"101,North,500.0,5\n"
            b"102,South,350.0,3\n"
            b"103,North,750.0,7\n"
            b"104,East,200.0,2\n"
            b"105,West,900.0,9\n"
        )

        upload_res = self.client.post(
            f"/projects/{project_id}/datasets",
            files={"file": ("ecommerce_orders.csv", io.BytesIO(csv_content), "text/csv")},
        )
        self.assertEqual(upload_res.status_code, 201)
        ds_data = upload_res.json()
        dataset_id = ds_data["id"]
        self.assertEqual(ds_data["filename"], "ecommerce_orders.csv")
        self.assertEqual(ds_data["processing_status"], "READY")
        self.assertEqual(ds_data["row_count"], 5)
        self.assertEqual(ds_data["column_count"], 4)

        # 3. Check dataset status and profile
        status_res = self.client.get(f"/datasets/{dataset_id}/status")
        self.assertEqual(status_res.status_code, 200)
        self.assertEqual(status_res.json()["processing_status"], "READY")

        profile_res = self.client.get(f"/datasets/{dataset_id}/profile")
        self.assertEqual(profile_res.status_code, 200)
        self.assertIn("profile", profile_res.json())
        self.assertIn("semantic_schema", profile_res.json())

        # 4. Analyze dataset
        analyze_res = self.client.post(
            f"/datasets/{dataset_id}/analyze",
            json={"question": "What is the total revenue by region?"},
        )
        self.assertEqual(analyze_res.status_code, 201)
        analysis = analyze_res.json()
        analysis_id = analysis["analysis_id"]
        self.assertTrue(analysis["success"])
        self.assertIn("North", analysis["insight"] or "")
        self.assertGreater(analysis["total_latency_ms"], 0)

        # 5. Retrieve analysis run, SQL, results
        run_res = self.client.get(f"/analyses/{analysis_id}")
        self.assertEqual(run_res.status_code, 200)
        self.assertEqual(run_res.json()["analysis_id"], analysis_id)

        sql_res = self.client.get(f"/analyses/{analysis_id}/sql")
        self.assertEqual(sql_res.status_code, 200)
        self.assertIn("SELECT", sql_res.json()["sql"].upper())

        res_res = self.client.get(f"/analyses/{analysis_id}/results")
        self.assertEqual(res_res.status_code, 200)
        self.assertGreaterEqual(len(res_res.json()["result_preview"]), 1)

        # 6. Generate 10-section analytical report
        rep_res = self.client.post(
            "/reports",
            json={
                "dataset_id": dataset_id,
                "project_id": project_id,
                "title": "Quarterly Revenue Breakdown",
                "analysis_ids": [analysis_id],
            },
        )
        self.assertEqual(rep_res.status_code, 201)
        rep = rep_res.json()
        report_id = rep["id"]
        self.assertEqual(rep["title"], "Quarterly Revenue Breakdown")
        self.assertIn("sections", rep["content"])
        self.assertIn("## 1. Executive Dataset Overview", rep["markdown"])
        self.assertIn("## 10. Analytical Limitations & Governance Caveats", rep["markdown"])

        # 7. Get report by ID and list reports
        get_rep = self.client.get(f"/reports/{report_id}")
        self.assertEqual(get_rep.status_code, 200)
        self.assertEqual(get_rep.json()["id"], report_id)

        list_rep = self.client.get(f"/reports?project_id={project_id}")
        self.assertEqual(list_rep.status_code, 200)
        self.assertGreaterEqual(len(list_rep.json()), 1)

    def test_legacy_session_endpoints_preserved(self):
        # Test legacy /upload endpoint
        csv_data = b"product,sales\nWidget A,150\nWidget B,250\n"
        upload_res = self.client.post(
            "/upload",
            files={"file": ("legacy_sales.csv", io.BytesIO(csv_data), "text/csv")},
        )
        self.assertEqual(upload_res.status_code, 200)
        session_id = upload_res.json()["session_id"]
        self.assertTrue(session_id)

        # Test legacy /tables/{session_id}
        tables_res = self.client.get(f"/tables/{session_id}")
        self.assertEqual(tables_res.status_code, 200)
        self.assertGreaterEqual(len(tables_res.json()["tables"]), 1)

        # Test legacy /query
        query_res = self.client.post(
            "/query",
            json={"session_id": session_id, "question": "What are the total sales?"},
        )
        self.assertEqual(query_res.status_code, 200)
        self.assertTrue(query_res.json()["success"])

        # Test legacy /session/{session_id} deletion
        del_res = self.client.delete(f"/session/{session_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json()["status"], "deleted")


if __name__ == "__main__":
    unittest.main()
