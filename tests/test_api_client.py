"""
Unit tests for frontend/api_client.py.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from frontend.api_client import DataPilotApiClient, VeridexApiClient


class TestApiClient(unittest.TestCase):
    def setUp(self):
        self.client = VeridexApiClient(base_url="http://localhost:8000")

    def test_backward_compatibility_alias(self):
        self.assertIs(DataPilotApiClient, VeridexApiClient)

    @patch("urllib.request.urlopen")
    def test_health_check_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"status": "healthy", "llm_provider": "mock", "database_backend": "sqlite", "active_sessions": 1}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        health = self.client.health()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["llm_provider"], "mock")

    @patch("urllib.request.urlopen")
    def test_query_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"question": "What is revenue?", "success": True, "sql": "SELECT SUM(revenue) FROM sales"}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = self.client.query("What is revenue?", session_id="test-session-123")
        self.assertTrue(res["success"])
        self.assertIn("SELECT", res["sql"])

    @patch("urllib.request.urlopen")
    def test_upload_file_constructs_multipart(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"session_id": "sess-1", "table": "sales", "row_count": 10, "columns": ["revenue"]}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = self.client.upload_file(b"revenue\n100\n", "sales.csv")
        self.assertEqual(res["table"], "sales")
        self.assertEqual(res["session_id"], "sess-1")

    @patch("urllib.request.urlopen")
    def test_connection_error_raises_friendly_exception(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        with self.assertRaises(ConnectionError) as ctx:
            self.client.health()
        self.assertIn("Cannot reach VERIDEX API", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
