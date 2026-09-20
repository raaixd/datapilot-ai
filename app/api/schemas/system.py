from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str = "2.0.0"
    timestamp: str | None = None
    llm_provider: str
    database_backend: str
    active_sessions: int = 0


class ReadyResponse(BaseModel):
    status: str
    database: dict[str, Any]
    storage: dict[str, Any]
    timestamp: str


class MetricsResponse(BaseModel):
    metrics: dict[str, Any]
