"""
Unit tests for the VERIDEX Observability Engine (app/core/observability.py).
"""

from __future__ import annotations

import json
import logging
import threading
import unittest

from app.core.config import Settings
from app.core.observability import MetricsCollector, StructuredJSONFormatter, get_metrics_collector, setup_observability


class TestStructuredJSONFormatter(unittest.TestCase):
    def setUp(self):
        self.formatter = StructuredJSONFormatter()

    def test_formats_standard_record_to_json(self):
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="User %s logged in",
            args=("alice",),
            exc_info=None,
        )
        output = self.formatter.format(record)
        data = json.loads(output)

        self.assertEqual(data["level"], "INFO")
        self.assertEqual(data["logger"], "test_logger")
        self.assertEqual(data["message"], "User alice logged in")
        self.assertIn("timestamp", data)

    def test_formats_extra_fields(self):
        record = logging.LogRecord(
            name="api_logger",
            level=logging.WARNING,
            pathname="api.py",
            lineno=10,
            msg="Slow query detected",
            args=(),
            exc_info=None,
        )
        record.request_id = "req-12345"
        record.duration_ms = 450.2

        output = self.formatter.format(record)
        data = json.loads(output)

        self.assertEqual(data["request_id"], "req-12345")
        self.assertEqual(data["duration_ms"], 450.2)
        self.assertEqual(data["level"], "WARNING")

    def test_formats_exception_traceback(self):
        try:
            raise ValueError("Invalid configuration parameter")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="err_logger",
            level=logging.ERROR,
            pathname="api.py",
            lineno=99,
            msg="Failed to process request",
            args=(),
            exc_info=exc_info,
        )
        output = self.formatter.format(record)
        data = json.loads(output)

        self.assertEqual(data["level"], "ERROR")
        self.assertIn("exception", data)
        self.assertIn("ValueError: Invalid configuration parameter", data["exception"])


class TestMetricsCollector(unittest.TestCase):
    def setUp(self):
        self.collector = MetricsCollector()

    def test_initial_summary_is_empty(self):
        summary = self.collector.get_metrics_summary()
        self.assertEqual(summary["total_requests"], 0)
        self.assertEqual(summary["total_errors"], 0)
        self.assertEqual(summary["error_rate"], 0.0)
        self.assertEqual(summary["sql_repair_count"], 0)
        self.assertEqual(summary["llm_fallback_count"], 0)
        self.assertEqual(summary["active_analyses"], 0)
        self.assertEqual(summary["latency_ms"]["p50"], 0.0)

    def test_record_requests_and_latency_percentiles(self):
        # Record latencies 10 to 100
        for i in range(1, 101):
            self.collector.record_request(
                path="/api/test",
                method="GET",
                status_code=200,
                latency_ms=float(i),
            )

        summary = self.collector.get_metrics_summary()
        self.assertEqual(summary["total_requests"], 100)
        self.assertEqual(summary["total_errors"], 0)
        self.assertEqual(summary["error_rate"], 0.0)
        self.assertEqual(summary["status_codes"]["200"], 100)
        self.assertEqual(summary["requests_by_endpoint"]["GET /api/test"], 100)

        lat = summary["latency_ms"]
        self.assertEqual(lat["min"], 1.0)
        self.assertEqual(lat["max"], 100.0)
        self.assertEqual(lat["p50"], 51.0)
        self.assertEqual(lat["p90"], 91.0)
        self.assertAlmostEqual(lat["avg"], 50.5, places=1)

    def test_error_rate_calculation(self):
        # 3 successes (200), 1 client error (400), 1 server error (500)
        self.collector.record_request("/health", "GET", 200, 10.0)
        self.collector.record_request("/projects", "POST", 201, 20.0)
        self.collector.record_request("/query", "POST", 200, 30.0)
        self.collector.record_request("/datasets/bad", "GET", 404, 5.0)
        self.collector.record_request("/analyze", "POST", 500, 50.0)

        summary = self.collector.get_metrics_summary()
        self.assertEqual(summary["total_requests"], 5)
        self.assertEqual(summary["total_errors"], 2)
        self.assertAlmostEqual(summary["error_rate"], 2 / 5, places=2)
        self.assertEqual(summary["status_codes"]["200"], 2)
        self.assertEqual(summary["status_codes"]["201"], 1)
        self.assertEqual(summary["status_codes"]["404"], 1)
        self.assertEqual(summary["status_codes"]["500"], 1)

    def test_sql_repairs_and_llm_fallbacks(self):
        self.collector.record_sql_repair()
        self.collector.record_sql_repair()
        self.collector.record_llm_fallback()
        self.collector.set_active_analyses(3)

        summary = self.collector.get_metrics_summary()
        self.assertEqual(summary["sql_repair_count"], 2)
        self.assertEqual(summary["llm_fallback_count"], 1)
        self.assertEqual(summary["active_analyses"], 3)

    def test_sliding_window_latency_limit(self):
        for i in range(1200):
            self.collector.record_request("/ping", "GET", 200, float(i))

        summary = self.collector.get_metrics_summary()
        self.assertEqual(summary["total_requests"], 1200)
        # Latency window should be capped at 1000
        self.assertGreaterEqual(summary["latency_ms"]["min"], 200.0)

    def test_concurrency_thread_safety(self):
        def worker():
            for _ in range(50):
                self.collector.record_request("/concurrent", "GET", 200, 15.0)
                self.collector.record_sql_repair()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        summary = self.collector.get_metrics_summary()
        self.assertEqual(summary["total_requests"], 250)
        self.assertEqual(summary["sql_repair_count"], 250)


class TestSetupObservability(unittest.TestCase):
    def test_setup_local_mode(self):
        settings = Settings(local_mode=True, cloudwatch_enabled=False)
        setup_observability(settings)
        # Global metrics collector is accessible
        collector = get_metrics_collector()
        self.assertIsInstance(collector, MetricsCollector)


if __name__ == "__main__":
    unittest.main()
