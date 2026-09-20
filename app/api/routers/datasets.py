"""
Datasets API router.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.schemas.datasets import DatasetProfileResponse, DatasetResponse, DatasetStatusResponse
from app.db.repositories.dataset_repo import DatasetRepository
from app.db.repositories.project_repo import ProjectRepository
from app.db.session import get_db, get_session_factory
from app.services.ingestion import IngestionService
from app.storage.base import profile_key, semantic_schema_key
from app.storage.factory import build_storage_backend_from_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["datasets"])


@router.post("/projects/{project_id}/datasets", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def upload_dataset(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> DatasetResponse:
    project_repo = ProjectRepository(db)
    project = project_repo.get_project(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    service = IngestionService(session_factory=get_session_factory(db.bind))
    ingested = service.ingest_file(
        project_id=project_id,
        filename=file.filename or "data.csv",
        file_bytes=content,
    )
    dataset = DatasetRepository(db).get_dataset(ingested.id) or ingested

    return DatasetResponse(
        id=dataset.id,
        project_id=dataset.project_id,
        filename=dataset.filename,
        file_type=dataset.file_type,
        s3_key=dataset.s3_key,
        file_size=dataset.file_size,
        row_count=dataset.row_count,
        column_count=dataset.column_count,
        processing_status=dataset.processing_status,
        processing_error=dataset.processing_error,
        created_at=dataset.created_at.isoformat(),
    )


@router.get("/datasets/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)) -> DatasetResponse:
    repo = DatasetRepository(db)
    dataset = repo.get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset '{dataset_id}' not found.",
        )
    return DatasetResponse(
        id=dataset.id,
        project_id=dataset.project_id,
        filename=dataset.filename,
        file_type=dataset.file_type,
        s3_key=dataset.s3_key,
        file_size=dataset.file_size,
        row_count=dataset.row_count,
        column_count=dataset.column_count,
        processing_status=dataset.processing_status,
        processing_error=dataset.processing_error,
        created_at=dataset.created_at.isoformat(),
    )


@router.get("/datasets/{dataset_id}/status", response_model=DatasetStatusResponse)
def get_dataset_status(dataset_id: str, db: Session = Depends(get_db)) -> DatasetStatusResponse:
    repo = DatasetRepository(db)
    dataset = repo.get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset '{dataset_id}' not found.",
        )
    return DatasetStatusResponse(
        id=dataset.id,
        processing_status=dataset.processing_status,
        processing_error=dataset.processing_error,
        row_count=dataset.row_count,
        column_count=dataset.column_count,
    )


@router.get("/datasets/{dataset_id}/profile", response_model=DatasetProfileResponse)
def get_dataset_profile(dataset_id: str, db: Session = Depends(get_db)) -> DatasetProfileResponse:
    repo = DatasetRepository(db)
    dataset = repo.get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset '{dataset_id}' not found.",
        )

    storage = build_storage_backend_from_settings()
    p_key = profile_key(dataset.project_id, dataset.id)
    s_key = semantic_schema_key(dataset.project_id, dataset.id)

    profile_data: dict[str, Any] = {}
    schema_data: dict[str, Any] = {}

    if storage.object_exists(p_key):
        try:
            profile_data = json.loads(storage.get_object(p_key).decode("utf-8"))
        except Exception as exc:
            logger.warning("Failed to decode profile.json for dataset %s: %s", dataset_id, exc)

    if storage.object_exists(s_key):
        try:
            schema_data = json.loads(storage.get_object(s_key).decode("utf-8"))
        except Exception as exc:
            logger.warning("Failed to decode semantic_schema.json for dataset %s: %s", dataset_id, exc)

    # Fallback to column records in database if storage artifacts not found
    if not schema_data and dataset.columns:
        schema_data = {
            "dataset_id": dataset.id,
            "row_count": dataset.row_count,
            "column_count": dataset.column_count,
            "columns": [
                {
                    "name": c.name,
                    "physical_type": c.physical_type,
                    "semantic_type": c.semantic_type,
                    "nullable": c.nullable,
                    "description": c.description,
                    "statistics": c.statistics_json,
                }
                for c in dataset.columns
            ],
        }

    return DatasetProfileResponse(
        id=dataset.id,
        profile=profile_data,
        semantic_schema=schema_data,
    )
