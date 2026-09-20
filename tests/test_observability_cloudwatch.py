"""
Unit tests for structured logging and CloudWatch observability integration.

Verifies:
  - JsonFormatter structured output containing contextual metadata
  - Zero AWS logging calls when LOCAL_MODE=true
  - Fail-safe fallback when CloudWatch client encounters errors
"""

from __future__ import annotations

import json
import logging
import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.core.logging_config import JsonFormatter, configure_logging


class TestCloudWatchObservability(unittest.TestCase):
    def test_json_formatter_structured_output(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="app.api.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Analysis completed successfully",
            args=(),
            exc_info=None,
        )
        record.request_id = "req-123"
        record.dataset_id = "ds-456"
        record.latency_ms = 45.2
        record.sql_repair_count = 1

        formatted = formatter.format(record)
        parsed = json.loads(formatted)

        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["logger"], "app.api.test")
        self.assertEqual(parsed["message"], "Analysis completed successfully")
        self.assertEqual(parsed["request_id"], "req-123")
        self.assertEqual(parsed["dataset_id"], "ds-456")
        self.assertEqual(parsed["latency_ms"], 45.2)
        self.assertEqual(parsed["sql_repair_count"], 1)
        self.assertIn("timestamp", parsed)

    @patch("app.core.logging_config.get_settings")
    def test_local_mode_true_does_not_attach_cloudwatch(self, mock_get_settings):
        mock_settings = Settings(
            local_mode=True,
            cloudwatch_enabled=True,  # Even if true, local_mode takes priority
            log_level="INFO",
        )
        mock_get_settings.return_value = mock_settings

        with patch("boto3.Session") as mock_session:
            configure_logging(level="INFO")
            mock_session.assert_not_called()

        root = logging.getLogger()
        self.assertTrue(len(root.handlers) >= 1)
        self.assertIsInstance(root.handlers[0], logging.StreamHandler)

    @patch("app.core.logging_config.get_settings")
    def test_cloudwatch_failsafe_fallback(self, mock_get_settings):
        mock_settings = Settings(
            local_mode=False,
            cloudwatch_enabled=True,
            cloudwatch_log_group="/veridex/api",
            aws_region="us-east-1",
            log_level="INFO",
        )
        mock_get_settings.return_value = mock_settings

        # Simulate boto3 failure
        with patch("boto3.Session", side_effect=RuntimeError("AWS credentials not found")):
            # Must not raise exception
            configure_logging(level="INFO")

        root = logging.getLogger()
        self.assertTrue(len(root.handlers) >= 1)


if __name__ == "__main__":
    unittest.main()
