"""
HTTP Client for communicating with the DataPilot AI FastAPI backend.

Used by frontend/streamlit_app.py when running in 'API Mode' or when
API_BASE_URL is configured, establishing clean architectural separation between
frontend presentation and backend state/orchestration.
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


class DataPilotApiClient:
    """Synchronous HTTP client for DataPilot AI FastAPI backend."""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
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
            raise ConnectionError(f"Cannot reach DataPilot API at {self.base_url}: {exc.reason}") from exc

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        boundary = "----WebKitFormBoundaryDataPilotUpload"
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
