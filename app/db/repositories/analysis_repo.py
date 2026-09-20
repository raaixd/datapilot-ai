"""
Repository for AnalysisRun, AnalysisQuery, AnalysisResultModel, and Insight entities.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.models import AnalysisQuery, AnalysisResultModel, AnalysisRun, Insight
from app.db.repositories.base import BaseRepository


class AnalysisRepository(BaseRepository):
    """Data access operations for AI analyses, SQL executions, and grounded insights."""

    def create_analysis_run(
        self,
        dataset_id: str,
        question: str,
        scope: str = "in_scope",
        llm_provider: str | None = None,
        analysis_id: str | None = None,
    ) -> AnalysisRun:
        kwargs: dict[str, Any] = {
            "dataset_id": dataset_id,
            "question": question.strip(),
            "status": "PENDING",
            "scope": scope,
            "llm_provider": llm_provider,
        }
        if analysis_id:
            kwargs["id"] = analysis_id

        run = AnalysisRun(**kwargs)
        self.session.add(run)
        self.session.flush()
        return run

    def get_analysis_run(self, analysis_id: str) -> AnalysisRun | None:
        stmt = select(AnalysisRun).where(AnalysisRun.id == analysis_id)
        return self.session.scalars(stmt).first()

    def list_analysis_runs(
        self, dataset_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[AnalysisRun]:
        stmt = select(AnalysisRun)
        if dataset_id:
            stmt = stmt.where(AnalysisRun.dataset_id == dataset_id)
        stmt = stmt.order_by(AnalysisRun.started_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def complete_analysis_run(
        self,
        analysis_id: str,
        latency_ms: int,
        status: str = "COMPLETED",
        llm_provider: str | None = None,
    ) -> AnalysisRun | None:
        run = self.get_analysis_run(analysis_id)
        if not run:
            return None
        run.status = status
        run.latency_ms = latency_ms
        run.completed_at = datetime.now(UTC)
        if llm_provider:
            run.llm_provider = llm_provider
        self.session.flush()
        return run

    def record_query(
        self,
        analysis_run_id: str,
        sql: str,
        execution_time_ms: int = 0,
        repair_count: int = 0,
        status: str = "SUCCESS",
        error: str | None = None,
        correction_history: list[dict[str, Any]] | None = None,
    ) -> AnalysisQuery:
        query = AnalysisQuery(
            analysis_run_id=analysis_run_id,
            sql=sql,
            execution_time_ms=execution_time_ms,
            repair_count=repair_count,
            status=status,
            error=error,
            correction_history_json=correction_history or [],
        )
        self.session.add(query)
        self.session.flush()
        return query

    def get_queries(self, analysis_run_id: str) -> list[AnalysisQuery]:
        stmt = select(AnalysisQuery).where(AnalysisQuery.analysis_run_id == analysis_run_id)
        return list(self.session.scalars(stmt).all())

    def record_result(
        self,
        analysis_run_id: str,
        row_count: int = 0,
        preview: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
        insight_text: str | None = None,
        chart_type: str | None = None,
        result_location: str | None = None,
    ) -> AnalysisResultModel:
        res = AnalysisResultModel(
            analysis_run_id=analysis_run_id,
            row_count=row_count,
            preview_json=preview or [],
            metrics_json=metrics or {},
            insight_text=insight_text,
            chart_type=chart_type,
            result_location=result_location,
        )
        self.session.add(res)
        self.session.flush()
        return res

    def get_result(self, analysis_run_id: str) -> AnalysisResultModel | None:
        stmt = select(AnalysisResultModel).where(AnalysisResultModel.analysis_run_id == analysis_run_id)
        return self.session.scalars(stmt).first()

    def record_insight(
        self,
        analysis_run_id: str,
        dataset_id: str,
        title: str,
        summary: str,
        findings: list[str] | None = None,
        sql_used: str | None = None,
    ) -> Insight:
        insight = Insight(
            analysis_run_id=analysis_run_id,
            dataset_id=dataset_id,
            title=title,
            summary=summary,
            findings_json=findings or [],
            sql_used=sql_used,
        )
        self.session.add(insight)
        self.session.flush()
        return insight

    def list_insights(self, dataset_id: str | None = None, limit: int = 50) -> list[Insight]:
        stmt = select(Insight)
        if dataset_id:
            stmt = stmt.where(Insight.dataset_id == dataset_id)
        stmt = stmt.order_by(Insight.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt).all())
