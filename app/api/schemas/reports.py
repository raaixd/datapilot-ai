from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReportCreateRequest(BaseModel):
    dataset_id: str = Field(..., description="Target dataset ID for the report")
    project_id: str | None = Field(None, description="Optional associated project ID")
    title: str | None = Field(None, description="Custom report title")
    analysis_ids: list[str] = Field(default_factory=list, description="IDs of analyses to include")


class ReportResponse(BaseModel):
    id: str
    title: str
    project_id: str | None = None
    dataset_id: str | None = None
    summary: str | None = None
    s3_key: str | None = None
    content: dict[str, Any] | None = None
    markdown: str | None = None
    created_at: str
