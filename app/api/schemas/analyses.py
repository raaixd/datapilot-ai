from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000, description="Natural language analysis question")
    session_id: str | None = Field(None, description="Optional analytical session identifier")


class AnalyzeResponse(BaseModel):
    analysis_id: str
    dataset_id: str
    question: str
    success: bool
    insight: str | None = None
    chart_type: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    llm_latency_ms: int = 0
    sql_execution_time_ms: int = 0
    total_latency_ms: int = 0
    retry_count: int = 0


class AnalysisSQLResponse(BaseModel):
    analysis_id: str
    sql: str | None = None
    status: str
    retry_count: int = 0
    sql_error_type: str | None = None
    correction_history: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisResultsResponse(BaseModel):
    analysis_id: str
    result_preview: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    chart_type: str | None = None
