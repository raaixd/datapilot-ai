"""
HTTP Client for communicating with the VERIDEX FastAPI backend.

Used by frontend/streamlit_app.py to interact with the Stage 4 REST API,
establishing clean architectural separation between frontend presentation
and backend state/orchestration.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class APIClientError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class VeridexApiClient:
    """Synchronous HTTP client for VERIDEX FastAPI backend."""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 45.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None}
            if clean_params:
                url += "?" + urllib.parse.urlencode(clean_params)

        hdrs = headers or {}
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            try:
                err_body = json.loads(exc.read().decode("utf-8"))
                detail = err_body.get("detail", str(exc))
            except Exception:
                detail = exc.reason
            logger.warning("API call to %s failed with %d: %s", url, exc.code, detail)
            raise APIClientError(exc.code, str(detail)) from exc
        except urllib.error.URLError as exc:
            logger.warning("Could not connect to API at %s: %s", url, exc.reason)
            raise ConnectionError(f"Cannot reach VERIDEX API at {self.base_url}: {exc.reason}") from exc

    # -------------------------------------------------------------------------
    # System & Observability Endpoints
    # -------------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Check API liveness."""
        return self._request("GET", "/health")

    def ready(self) -> dict[str, Any]:
        """Check API and storage/database readiness."""
        return self._request("GET", "/ready")

    def metrics(self) -> dict[str, Any]:
        """Retrieve operational observability metrics (latencies, counts, errors)."""
        return self._request("GET", "/metrics")

    # -------------------------------------------------------------------------
    # Projects Endpoints
    # -------------------------------------------------------------------------
    def list_projects(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """List all projects."""
        return self._request("GET", "/projects", params={"limit": limit, "offset": offset})

    def create_project(self, name: str, description: str | None = None) -> dict[str, Any]:
        """Create a new project workspace."""
        payload = json.dumps({"name": name.strip(), "description": description.strip() if description else None}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        return self._request("POST", "/projects", data=payload, headers=headers)

    def get_project(self, project_id: str) -> dict[str, Any]:
        """Retrieve metadata for a specific project."""
        return self._request("GET", f"/projects/{project_id}")

    # -------------------------------------------------------------------------
    # Datasets Endpoints
    # -------------------------------------------------------------------------
    def upload_project_dataset(
        self,
        project_id: str,
        file_bytes: bytes,
        filename: str,
    ) -> dict[str, Any]:
        """Upload and trigger deterministic ingestion profiling for a dataset."""
        boundary = "----WebKitFormBoundaryVeridexProjectUpload"
        body = io.BytesIO()
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
        body.write(b"Content-Type: application/octet-stream\r\n\r\n")
        body.write(file_bytes)
        body.write(f"\r\n--{boundary}--\r\n".encode())

        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        return self._request("POST", f"/projects/{project_id}/datasets", data=body.getvalue(), headers=headers)

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        """Retrieve dataset entity metadata."""
        return self._request("GET", f"/datasets/{dataset_id}")

    def get_dataset_status(self, dataset_id: str) -> dict[str, Any]:
        """Poll dataset processing status."""
        return self._request("GET", f"/datasets/{dataset_id}/status")

    def get_dataset_profile(self, dataset_id: str) -> dict[str, Any]:
        """Retrieve computed profile and semantic schema metadata."""
        return self._request("GET", f"/datasets/{dataset_id}/profile")

    # -------------------------------------------------------------------------
    # Analyses Endpoints
    # -------------------------------------------------------------------------
    def analyze_dataset(self, dataset_id: str, question: str) -> dict[str, Any]:
        """Submit natural-language analytical question for self-correcting SQL execution."""
        payload = json.dumps({"question": question.strip()}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        return self._request("POST", f"/datasets/{dataset_id}/analyze", data=payload, headers=headers)

    def get_analysis(self, analysis_id: str) -> dict[str, Any]:
        """Retrieve details of an analysis run."""
        return self._request("GET", f"/analyses/{analysis_id}")

    def get_analysis_sql(self, analysis_id: str) -> dict[str, Any]:
        """Retrieve executed SQL and repair history."""
        return self._request("GET", f"/analyses/{analysis_id}/sql")

    def get_analysis_results(self, analysis_id: str) -> dict[str, Any]:
        """Retrieve computed tabular result preview and metrics."""
        return self._request("GET", f"/analyses/{analysis_id}/results")

    # -------------------------------------------------------------------------
    # Reports Endpoints
    # -------------------------------------------------------------------------
    def create_report(
        self,
        dataset_id: str,
        project_id: str | None = None,
        title: str | None = None,
        analysis_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Compile a grounded 10-section analytical report."""
        payload = json.dumps({
            "dataset_id": dataset_id,
            "project_id": project_id,
            "title": title,
            "analysis_ids": analysis_ids or [],
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        return self._request("POST", "/reports", data=payload, headers=headers)

    def get_report(self, report_id: str) -> dict[str, Any]:
        """Retrieve compiled report JSON and Markdown."""
        return self._request("GET", f"/reports/{report_id}")

    def list_reports(
        self,
        project_id: str | None = None,
        dataset_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List reports filtered by project or dataset."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if project_id:
            params["project_id"] = project_id
        if dataset_id:
            params["dataset_id"] = dataset_id
        return self._request("GET", "/reports", params=params)

    # -------------------------------------------------------------------------
    # Legacy Endpoints (Backward Compatibility)
    # -------------------------------------------------------------------------
    def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        boundary = "----WebKitFormBoundaryVeridexUpload"
        body = io.BytesIO()

        if session_id:
            body.write(f"--{boundary}\r\n".encode())
            body.write(b'Content-Disposition: form-data; name="session_id"\r\n\r\n')
            body.write(f"{session_id}\r\n".encode())

        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
        body.write(b"Content-Type: application/octet-stream\r\n\r\n")
        body.write(file_bytes)
        body.write(f"\r\n--{boundary}--\r\n".encode())

        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        return self._request("POST", "/upload", data=body.getvalue(), headers=headers)

    def query(self, question: str, session_id: str) -> dict[str, Any]:
        payload = json.dumps({"question": question, "session_id": session_id}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        return self._request("POST", "/query", data=payload, headers=headers)

    def get_tables(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/tables/{session_id}")

    def end_session(self, session_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/session/{session_id}")


# Backward compatibility alias
DataPilotApiClient = VeridexApiClient
