"""
Centralized structured logging and CloudWatch integration.

Supports:
  - Human-readable console formatting for local development
  - Structured JSON formatting for machine ingestion (CloudWatch / Datadog)
  - Optional CloudWatch Logs handler when LOCAL_MODE=false and CLOUDWATCH_ENABLED=true
  - Fail-safe fallback (never crashes application if CloudWatch is unreachable)
  - Strict $0 AWS guarantee: zero AWS logging calls when LOCAL_MODE=true
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from app.core.config import get_settings


class JsonFormatter(logging.Formatter):
    """Structured JSON formatter for production log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include contextual metadata if present on record
        for key in (
            "request_id",
            "dataset_id",
            "analysis_id",
            "project_id",
            "latency_ms",
            "error_code",
            "sql_repair_count",
        ):
            val = getattr(record, key, None)
            if val is not None:
                log_obj[key] = val

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


def configure_logging(
    level: str | None = None,
    use_json: bool | None = None,
) -> None:
    """Configure root logging and optional CloudWatch integration."""
    settings = get_settings()
    resolved_level_str = (level or settings.log_level).upper()
    resolved_level = getattr(logging, resolved_level_str, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_level)

    # Clear existing handlers to prevent duplicate lines
    root_logger.handlers.clear()

    # Determine formatter
    should_use_json = (
        use_json
        if use_json is not None
        else os.getenv("LOG_FORMAT", "text").lower() == "json"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    if should_use_json:
        console_handler.setFormatter(JsonFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    root_logger.addHandler(console_handler)

    # CloudWatch Logs handler (Production only, LOCAL_MODE=false)
    if not settings.local_mode and settings.cloudwatch_enabled:
        try:
            import boto3  # type: ignore[import]
            import watchtower  # type: ignore[import]

            session_kwargs: dict[str, Any] = {}
            if settings.aws_profile:
                session_kwargs["profile_name"] = settings.aws_profile
            session = boto3.Session(**session_kwargs) if session_kwargs else boto3.Session()
            logs_client = session.client("logs", region_name=settings.aws_region)

            cw_handler = watchtower.CloudWatchLogHandler(
                log_group_name=settings.cloudwatch_log_group,
                boto3_client=logs_client,
                send_interval=5,
            )
            cw_handler.setFormatter(JsonFormatter())
            root_logger.addHandler(cw_handler)
            root_logger.info(
                "Attached CloudWatchLogHandler (log_group=%s, region=%s)",
                settings.cloudwatch_log_group,
                settings.aws_region,
            )
        except Exception as exc:
            # Fail-safe: log warning locally, never crash the service
            root_logger.warning(
                "Could not attach CloudWatchLogHandler (%s: %s). Falling back to console logging.",
                type(exc).__name__,
                exc,
            )
