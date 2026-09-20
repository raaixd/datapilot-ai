from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DatasetResponse(BaseModel):
    id: str
    project_id: str | None = None
    filename: str
    file_type: str
    s3_key: str
    file_size: int
    row_count: int | None = None
    column_count: int | None = None
    processing_status: str
    processing_error: str | None = None
    created_at: str


class DatasetStatusResponse(BaseModel):
    id: str
    processing_status: str
    processing_error: str | None = None
    row_count: int | None = None
    column_count: int | None = None


class DatasetProfileResponse(BaseModel):
    id: str
    profile: dict[str, Any]
    semantic_schema: dict[str, Any]


class DatasetListResponse(BaseModel):
    datasets: list[DatasetResponse]
    total: int
