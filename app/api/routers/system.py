"""
System API router for health, readiness, and metrics endpoints.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.schemas.system import HealthResponse, MetricsResponse, ReadyResponse
from app.core.config import get_settings
from app.core.observability import get_metrics_collector
from app.db.session import get_db
from app.storage.factory import build_storage_backend_from_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


_active_sessions_getter = None


def set_active_sessions_getter(getter) -> None:
    global _active_sessions_getter
    _active_sessions_getter = getter


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Basic liveness check."""
    settings = get_settings()
    active_count = _active_sessions_getter() if _active_sessions_getter else 0
    return HealthResponse(
        status="healthy",
        version="2.0.0",
        timestamp=datetime.now(UTC).isoformat(),
        llm_provider=settings.llm_provider,
        database_backend="postgresql" if not settings.local_mode else "sqlite",
        active_sessions=active_count,
    )


@router.get("/ready", response_model=ReadyResponse)
def readiness_check(
    response: Response,
    db: Session = Depends(get_db),
) -> ReadyResponse:
    """Readiness probe checking database and storage connectivity."""
    settings = get_settings()
    is_ready = True
    db_status: dict[str, Any] = {"status": "connected", "backend": "sqlite" if settings.local_mode else "postgresql"}
    storage_status: dict[str, Any] = {"status": "connected", "backend": "local" if settings.local_mode else "s3"}

    # Check database
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        is_ready = False
        db_status = {"status": "unhealthy", "error": str(exc)}
        logger.error("Readiness check DB failure: %s", exc)

    # Check storage
    try:
        storage = build_storage_backend_from_settings()
        # Verify storage is accessible
        _ = storage.object_exists("__health_check__")
    except Exception as exc:
        is_ready = False
        storage_status = {"status": "unhealthy", "error": str(exc)}
        logger.error("Readiness check storage failure: %s", exc)

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadyResponse(
        status="ready" if is_ready else "not_ready",
        database=db_status,
        storage=storage_status,
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics() -> MetricsResponse:
    """Platform metrics summary including latencies (p50/p90/p99) and error rates."""
    collector = get_metrics_collector()
    return MetricsResponse(metrics=collector.get_metrics_summary())
