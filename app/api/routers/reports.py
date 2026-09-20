"""
Reports API router.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.agents.orchestrator import AnalysisResult
from app.api.schemas.reports import ReportCreateRequest, ReportResponse
from app.data.loader import load_tabular_file
from app.data.profiler import DataProfiler
from app.data.semantic_schema import extract_semantic_schema
from app.db.repositories.analysis_repo import AnalysisRepository
from app.db.repositories.dataset_repo import DatasetRepository
from app.db.repositories.project_repo import ProjectRepository
from app.db.repositories.report_repo import ReportRepository
from app.db.session import get_db
from app.reports.analytical_report import AnalyticalReport, generate_analytical_report
from app.storage.factory import build_storage_backend_from_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])


@router.post("/reports", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: ReportCreateRequest,
    db: Session = Depends(get_db),
) -> ReportResponse:
    dataset_repo = DatasetRepository(db)
    dataset = dataset_repo.get_dataset(payload.dataset_id)
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset '{payload.dataset_id}' not found.",
        )

    project_id = payload.project_id or dataset.project_id
    project_repo = ProjectRepository(db)
    project = project_repo.get_project(project_id) if project_id else None
    project_name = project.name if project else "Default Project"

    storage = build_storage_backend_from_settings()

    profile = None
    semantic_schema = None

    if dataset.s3_key and storage.object_exists(dataset.s3_key):
        try:
            file_bytes = storage.get_object(dataset.s3_key)
            df = load_tabular_file(file_bytes, dataset.filename)
            profiler = DataProfiler()
            profile = profiler.profile(df, dataset_name=dataset.filename)
            semantic_schema = extract_semantic_schema(df, profile, dataset_id=dataset.id)
        except Exception as exc:
            logger.warning("Could not load dataframe for profiling report: %s", exc)

    # Collect analysis results
    analysis_repo = AnalysisRepository(db)
    analysis_results: list[AnalysisResult] = []

    target_runs = []
    if payload.analysis_ids:
        for aid in payload.analysis_ids:
            run = analysis_repo.get_analysis_run(aid)
            if run:
                target_runs.append(run)
    else:
        target_runs = analysis_repo.list_analysis_runs(dataset_id=dataset.id, limit=20)

    for run in target_runs:
        query = run.queries[0] if run.queries else None
        res = run.result
        analysis_results.append(
            AnalysisResult(
                question=run.question,
                sql=query.sql if query else "",
                success=run.status == "COMPLETED",
                insight=res.insight_text if res else "",
                chart_type=res.chart_type if res else None,
                metrics=res.metrics_json if res else {},
                error=None if run.status == "COMPLETED" else run.status,
                retry_count=query.repair_count if query else 0,
                sql_execution_time_ms=query.execution_time_ms if query else 0,
                total_latency_ms=run.latency_ms or 0,
            )
        )

    report_title = payload.title or f"Analytical Report - {dataset.filename}"
    analytical_report = generate_analytical_report(
        dataset_name=dataset.filename,
        profile=profile,
        semantic_schema=semantic_schema,
        analysis_results=analysis_results,
        project_name=project_name,
    )

    s3_key = f"reports/{analytical_report.report_id}.json"
    try:
        storage.put_object(
            key=s3_key,
            data=json.dumps(analytical_report.to_dict()).encode("utf-8"),
            content_type="application/json",
        )
    except Exception as exc:
        logger.warning("Failed to persist report to storage backend: %s", exc)

    overview = analytical_report.sections.get("overview", {})
    summary = overview.get("summary", "")

    report_repo = ReportRepository(db)
    db_report = report_repo.create_report(
        report_id=analytical_report.report_id,
        project_id=project_id or "default",
        dataset_id=dataset.id,
        title=report_title,
        summary=summary,
        content=analytical_report.to_dict(),
        s3_key=s3_key,
    )
    db.commit()
    db.refresh(db_report)

    return ReportResponse(
        id=db_report.id,
        title=db_report.title,
        project_id=db_report.project_id,
        dataset_id=db_report.dataset_id,
        summary=db_report.summary,
        s3_key=db_report.s3_key,
        content=analytical_report.to_dict(),
        markdown=analytical_report.to_markdown(),
        created_at=db_report.created_at.isoformat() if db_report.created_at else "",
    )


@router.get("/reports/{report_id}", response_model=ReportResponse)
def get_report(report_id: str, db: Session = Depends(get_db)) -> ReportResponse:
    report_repo = ReportRepository(db)
    db_report = report_repo.get_report(report_id)
    if not db_report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report '{report_id}' not found.",
        )

    content = db_report.content_json or {}
    markdown = None
    if content and "sections" in content:
        try:
            ar = AnalyticalReport(
                report_id=db_report.id,
                project_name=content.get("project_name", "Project"),
                dataset_name=content.get("dataset_name", "Dataset"),
                generated_at=content.get("generated_at", ""),
                sections=content.get("sections", {}),
            )
            markdown = ar.to_markdown()
        except Exception:
            markdown = None

    return ReportResponse(
        id=db_report.id,
        title=db_report.title,
        project_id=db_report.project_id,
        dataset_id=db_report.dataset_id,
        summary=db_report.summary,
        s3_key=db_report.s3_key,
        content=content,
        markdown=markdown,
        created_at=db_report.created_at.isoformat() if db_report.created_at else "",
    )


@router.get("/reports", response_model=list[ReportResponse])
def list_reports(
    project_id: str | None = Query(None, description="Filter by project ID"),
    dataset_id: str | None = Query(None, description="Filter by dataset ID"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[ReportResponse]:
    report_repo = ReportRepository(db)
    reports = report_repo.list_reports(
        project_id=project_id,
        dataset_id=dataset_id,
        limit=limit,
        offset=offset,
    )
    result = []
    for r in reports:
        result.append(
            ReportResponse(
                id=r.id,
                title=r.title,
                project_id=r.project_id,
                dataset_id=r.dataset_id,
                summary=r.summary,
                s3_key=r.s3_key,
                content=r.content_json,
                markdown=None,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
        )
    return result
