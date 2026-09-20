"""
Analyses API router.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agents.orchestrator import Orchestrator
from app.api.schemas.analyses import (
    AnalysisResultsResponse,
    AnalysisSQLResponse,
    AnalyzeRequest,
    AnalyzeResponse,
)
from app.data.database import AnalyticalDatabase
from app.data.loader import load_tabular_file
from app.data.profiler import DataProfiler
from app.data.semantic_schema import extract_semantic_schema
from app.db.repositories.analysis_repo import AnalysisRepository
from app.db.repositories.dataset_repo import DatasetRepository
from app.db.session import get_db
from app.llm.factory import build_llm_client
from app.storage.factory import build_storage_backend_from_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analyses"])


@router.post("/datasets/{dataset_id}/analyze", response_model=AnalyzeResponse, status_code=status.HTTP_201_CREATED)
def analyze_dataset(
    dataset_id: str,
    payload: AnalyzeRequest,
    db: Session = Depends(get_db),
) -> AnalyzeResponse:
    dataset_repo = DatasetRepository(db)
    dataset = dataset_repo.get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset '{dataset_id}' not found.",
        )

    storage = build_storage_backend_from_settings()
    if not storage.object_exists(dataset.s3_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Underlying data file '{dataset.s3_key}' not found in storage.",
        )

    file_bytes = storage.get_object(dataset.s3_key)
    try:
        df = load_tabular_file(file_bytes, dataset.filename)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to load dataset: {exc}",
        ) from exc

    table_name = dataset.filename.rsplit(".", 1)[0].replace("-", "_").replace(" ", "_")
    analytical_db = AnalyticalDatabase(backend="sqlite", path=":memory:")
    analytical_db.load_dataframe(df, table_name)

    # Load profiling metadata
    profiler = DataProfiler()
    profile = profiler.profile(df, dataset_name=table_name)
    semantic_schema = extract_semantic_schema(df, profile, dataset_id=table_name)

    # Initialize analysis tracking
    analysis_repo = AnalysisRepository(db)
    run = analysis_repo.create_analysis_run(
        dataset_id=dataset_id,
        question=payload.question,
    )

    llm_client = build_llm_client()
    orchestrator = Orchestrator(analytical_db, llm_client)

    result = orchestrator.analyze(
        question=payload.question,
        data_profile=profile,
        semantic_schema=semantic_schema,
    )

    # Record queries and results
    if result.retry_count > 0:
        from app.core.observability import get_metrics_collector
        get_metrics_collector().record_sql_repair()

    analysis_repo.create_analysis_query(
        analysis_id=run.id,
        sql=result.sql or "",
        execution_time_ms=result.sql_execution_time_ms,
        repair_count=result.retry_count,
        status="SUCCESS" if result.success else "FAILED",
        error=result.error if not result.success else None,
        correction_history=result.correction_history,
    )

    analysis_repo.create_analysis_result(
        analysis_id=run.id,
        row_count=len(result.result_preview),
        result_preview=result.result_preview,
        metrics=result.metrics,
        insight_text=result.insight,
        chart_type=result.chart_type,
    )

    analysis_repo.complete_analysis_run(
        analysis_id=run.id,
        latency_ms=result.total_latency_ms,
        status="COMPLETED" if result.success else "FAILED",
        llm_provider=result.llm_provider,
    )
    db.commit()

    return AnalyzeResponse(
        analysis_id=run.id,
        dataset_id=dataset.id,
        question=result.question,
        success=result.success,
        insight=result.insight,
        chart_type=result.chart_type,
        metrics=result.metrics,
        error=result.error,
        llm_latency_ms=result.llm_latency_ms,
        sql_execution_time_ms=result.sql_execution_time_ms,
        total_latency_ms=result.total_latency_ms,
        retry_count=result.retry_count,
    )


@router.get("/analyses/{analysis_id}", response_model=AnalyzeResponse)
def get_analysis(analysis_id: str, db: Session = Depends(get_db)) -> AnalyzeResponse:
    analysis_repo = AnalysisRepository(db)
    run = analysis_repo.get_analysis_run(analysis_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Analysis '{analysis_id}' not found.",
        )

    res = run.result
    return AnalyzeResponse(
        analysis_id=run.id,
        dataset_id=run.dataset_id,
        question=run.question,
        success=run.status == "COMPLETED",
        insight=res.insight_text if res else None,
        chart_type=res.chart_type if res else None,
        metrics=res.metrics_json if res else {},
        error=run.status if run.status != "COMPLETED" else None,
        llm_latency_ms=0,
        sql_execution_time_ms=run.queries[0].execution_time_ms if run.queries else 0,
        total_latency_ms=run.latency_ms or 0,
        retry_count=run.queries[0].repair_count if run.queries else 0,
    )


@router.get("/analyses/{analysis_id}/sql", response_model=AnalysisSQLResponse)
def get_analysis_sql(analysis_id: str, db: Session = Depends(get_db)) -> AnalysisSQLResponse:
    analysis_repo = AnalysisRepository(db)
    run = analysis_repo.get_analysis_run(analysis_id)
    if not run or not run.queries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SQL queries for analysis '{analysis_id}' not found.",
        )
    query = run.queries[0]
    return AnalysisSQLResponse(
        analysis_id=run.id,
        sql=query.sql,
        status=query.status,
        retry_count=query.repair_count,
        correction_history=query.correction_history_json or [],
    )


@router.get("/analyses/{analysis_id}/results", response_model=AnalysisResultsResponse)
def get_analysis_results(analysis_id: str, db: Session = Depends(get_db)) -> AnalysisResultsResponse:
    analysis_repo = AnalysisRepository(db)
    run = analysis_repo.get_analysis_run(analysis_id)
    if not run or not run.result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Results for analysis '{analysis_id}' not found.",
        )
    res = run.result
    return AnalysisResultsResponse(
        analysis_id=run.id,
        result_preview=res.preview_json or [],
        metrics=res.metrics_json or {},
        chart_type=res.chart_type,
    )
