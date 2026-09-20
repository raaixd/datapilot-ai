"""
VERIDEX Observability and Metrics Engine.

Provides structured JSON logging, CloudWatch log stream integration, and
in-memory thread-safe metrics collection for operational and analytical monitoring.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class StructuredJSONFormatter(logging.Formatter):
    """Formats log records as structured single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include all custom/extra attributes attached to the LogRecord
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
        }
        for key, val in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_entry[key] = val

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class MetricsCollector:
    """Thread-safe collector for API, SQL, LLM, and pipeline execution metrics."""

    def __init__(self, max_latency_samples: int = 1000):
        self._lock = threading.Lock()
        self._max_samples = max_latency_samples
        self.reset()

    def reset(self) -> None:
        """Reset all tracked metrics to zero (useful for test isolation)."""
        with getattr(self, "_lock", threading.Lock()):
            self._total_requests: int = 0
            self._total_errors: int = 0
            self._requests_by_endpoint: dict[str, int] = {}
            self._status_codes: dict[str, int] = {}
            self._latencies_ms: list[float] = []
            self._sql_repair_count: int = 0
            self._llm_fallback_count: int = 0
            self._active_analyses: int = 0
            self._start_time: float = time.time()

    def record_request(
        self,
        method_or_path: str = "GET",
        path_or_method: str = "/",
        status_code: int = 200,
        latency_ms: float = 0.0,
        **kwargs: Any,
    ) -> None:
        method = kwargs.get("method")
        path = kwargs.get("path")
        if not method or not path:
            if method_or_path.startswith("/") and not path_or_method.startswith("/"):
                path, method = method_or_path, path_or_method
            else:
                method, path = method_or_path, path_or_method

        with self._lock:
            self._total_requests += 1
            if status_code >= 400:
                self._total_errors += 1

            endpoint_key = f"{method.upper()} {path}"
            self._requests_by_endpoint[endpoint_key] = self._requests_by_endpoint.get(endpoint_key, 0) + 1
            sc_key = str(status_code)
            self._status_codes[sc_key] = self._status_codes.get(sc_key, 0) + 1

            self._latencies_ms.append(float(latency_ms))
            if len(self._latencies_ms) > self._max_samples:
                self._latencies_ms.pop(0)

    def record_sql_repair(self) -> None:
        with self._lock:
            self._sql_repair_count += 1

    def record_llm_fallback(self) -> None:
        with self._lock:
            self._llm_fallback_count += 1

    def set_active_analyses(self, count: int) -> None:
        with self._lock:
            self._active_analyses = max(0, count)

    def get_metrics_summary(self) -> dict[str, Any]:
        with self._lock:
            uptime = time.time() - self._start_time
            count = len(self._latencies_ms)
            if count > 0:
                sorted_lat = sorted(self._latencies_ms)
                p50 = sorted_lat[int(count * 0.50)]
                p90 = sorted_lat[int(count * 0.90)]
                p99 = sorted_lat[min(int(count * 0.99), count - 1)]
                avg_lat = sum(sorted_lat) / count
                min_lat = sorted_lat[0]
                max_lat = sorted_lat[-1]
            else:
                p50 = p90 = p99 = avg_lat = min_lat = max_lat = 0.0

            error_rate = (self._total_errors / self._total_requests) if self._total_requests > 0 else 0.0

            return {
                "uptime_seconds": round(uptime, 2),
                "total_requests": self._total_requests,
                "total_errors": self._total_errors,
                "error_rate": round(error_rate, 4),
                "active_analyses": self._active_analyses,
                "sql_repair_count": self._sql_repair_count,
                "llm_fallback_count": self._llm_fallback_count,
                "latency_ms": {
                    "p50": round(p50, 2),
                    "p90": round(p90, 2),
                    "p99": round(p99, 2),
                    "avg": round(avg_lat, 2),
                    "min": round(min_lat, 2),
                    "max": round(max_lat, 2),
                },
                "status_codes": dict(self._status_codes),
                "requests_by_endpoint": dict(self._requests_by_endpoint),
            }


_GLOBAL_METRICS = MetricsCollector()


def get_metrics_collector() -> MetricsCollector:
    """Access the global metrics collector singleton."""
    return _GLOBAL_METRICS


def setup_observability(settings: Settings | None = None) -> None:
    """Configure structured logging and optional CloudWatch log handlers."""
    cfg = settings or get_settings()

    # Create root or app handler with JSON formatter
    json_handler = logging.StreamHandler()
    json_handler.setFormatter(StructuredJSONFormatter())

    app_logger = logging.getLogger("app")
    app_logger.setLevel(cfg.log_level.upper())

    # Attach CloudWatch handler if in production mode and watchtower is available
    if not cfg.local_mode and cfg.cloudwatch_log_group:
        try:
            import boto3
            import watchtower

            boto_session = boto3.Session(
                region_name=cfg.aws_region,
                aws_access_key_id=cfg.aws_access_key_id,
                aws_secret_access_key=cfg.aws_secret_access_key,
            )
            cw_handler = watchtower.CloudWatchLogHandler(
                log_group=cfg.cloudwatch_log_group,
                boto3_client=boto_session.client("logs"),
                use_queues=True,
            )
            cw_handler.setFormatter(StructuredJSONFormatter())
            app_logger.addHandler(cw_handler)
            logger.info("CloudWatch logging configured on %s", cfg.cloudwatch_log_group)
        except Exception as exc:
            logger.warning("Could not initialize CloudWatch logger: %s (falling back to stdout)", exc)
