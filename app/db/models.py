"""
SQLAlchemy ORM models for VERIDEX metadata persistence.

Covers:
  - Projects
  - Datasets & Dataset Columns (semantic metadata)
  - Analysis Runs, Queries & Results
  - Insights
  - Reports
  - Evaluation Benchmarks

Compatible with both PostgreSQL (production RDS) and SQLite (LOCAL_MODE).
UUID primary keys are stored as 36-character strings for cross-backend portability.
JSON columns use SQLAlchemy's generic JSON type which works across SQLite and Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    """A logical workspace grouping datasets, analyses, and reports."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    # Relationships
    datasets: Mapped[list[Dataset]] = relationship("Dataset", back_populates="project", cascade="all, delete-orphan")
    reports: Mapped[list[ReportModel]] = relationship("ReportModel", back_populates="project", cascade="all, delete-orphan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Dataset(Base):
    """An uploaded or ingested dataset tracked within a project."""

    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)  # csv, xlsx, etc.
    s3_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # SHA-256 for idempotency
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processing_status: Mapped[str] = mapped_column(
        String(32), default="UPLOADED", nullable=False, index=True
    )  # UPLOADED, VALIDATING, PROCESSING, READY, FAILED
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )

    # Relationships
    project: Mapped[Project] = relationship("Project", back_populates="datasets")
    columns: Mapped[list[DatasetColumn]] = relationship(
        "DatasetColumn", back_populates="dataset", cascade="all, delete-orphan"
    )
    analysis_runs: Mapped[list[AnalysisRun]] = relationship(
        "AnalysisRun", back_populates="dataset", cascade="all, delete-orphan"
    )
    reports: Mapped[list[ReportModel]] = relationship(
        "ReportModel", back_populates="dataset", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "filename": self.filename,
            "file_type": self.file_type,
            "s3_key": self.s3_key,
            "file_size": self.file_size,
            "checksum": self.checksum,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "processing_status": self.processing_status,
            "processing_error": self.processing_error,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DatasetColumn(Base):
    """Machine-readable column profile and semantic schema metadata."""

    __tablename__ = "dataset_columns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    physical_type: Mapped[str] = mapped_column(String(64), nullable=False)  # int64, float64, object, etc.
    semantic_type: Mapped[str] = mapped_column(
        String(64), default="unknown", nullable=False
    )  # currency, percentage, identifier, categorical, datetime, numeric, text, boolean, unknown
    nullable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    statistics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Relationships
    dataset: Mapped[Dataset] = relationship("Dataset", back_populates="columns")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dataset_id": self.dataset_id,
            "name": self.name,
            "physical_type": self.physical_type,
            "semantic_type": self.semantic_type,
            "nullable": self.nullable,
            "description": self.description,
            "statistics": self.statistics_json or {},
        }


class AnalysisRun(Base):
    """An analytical question asked against a dataset."""

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)  # PENDING, COMPLETED, FAILED
    scope: Mapped[str] = mapped_column(String(32), default="in_scope", nullable=False)  # in_scope, ambiguous, out_of_scope, unsafe
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Relationships
    dataset: Mapped[Dataset] = relationship("Dataset", back_populates="analysis_runs")
    queries: Mapped[list[AnalysisQuery]] = relationship(
        "AnalysisQuery", back_populates="analysis_run", cascade="all, delete-orphan"
    )
    results: Mapped[list[AnalysisResultModel]] = relationship(
        "AnalysisResultModel", back_populates="analysis_run", cascade="all, delete-orphan"
    )
    insights: Mapped[list[Insight]] = relationship(
        "Insight", back_populates="analysis_run", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dataset_id": self.dataset_id,
            "question": self.question,
            "status": self.status,
            "scope": self.scope,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "latency_ms": self.latency_ms,
            "llm_provider": self.llm_provider,
        }


class AnalysisQuery(Base):
    """SQL query generated, executed, or repaired during an analysis run."""

    __tablename__ = "analysis_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    analysis_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    execution_time_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repair_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="SUCCESS", nullable=False)  # SUCCESS, FAILED
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    correction_history_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

    # Relationships
    analysis_run: Mapped[AnalysisRun] = relationship("AnalysisRun", back_populates="queries")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "analysis_run_id": self.analysis_run_id,
            "sql": self.sql,
            "execution_time_ms": self.execution_time_ms,
            "repair_count": self.repair_count,
            "status": self.status,
            "error": self.error,
            "correction_history": self.correction_history_json or [],
        }


class AnalysisResultModel(Base):
    """The computed results, metrics, and chart recommendations for an analysis run."""

    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    analysis_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    result_location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    preview_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    insight_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Relationships
    analysis_run: Mapped[AnalysisRun] = relationship("AnalysisRun", back_populates="results")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "analysis_run_id": self.analysis_run_id,
            "result_location": self.result_location,
            "row_count": self.row_count,
            "preview": self.preview_json or [],
            "metrics": self.metrics_json or {},
            "insight": self.insight_text,
            "chart_type": self.chart_type,
        }


class Insight(Base):
    """Grounded business findings extracted from an analysis."""

    __tablename__ = "insights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    analysis_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    findings_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    sql_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    # Relationships
    analysis_run: Mapped[AnalysisRun] = relationship("AnalysisRun", back_populates="insights")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "analysis_run_id": self.analysis_run_id,
            "dataset_id": self.dataset_id,
            "title": self.title,
            "summary": self.summary,
            "findings": self.findings_json or [],
            "sql_used": self.sql_used,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ReportModel(Base):
    """Persisted structured analytical report."""

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    # Relationships
    project: Mapped[Project] = relationship("Project", back_populates="reports")
    dataset: Mapped[Dataset] = relationship("Dataset", back_populates="reports")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "dataset_id": self.dataset_id,
            "title": self.title,
            "summary": self.summary,
            "content": self.content_json or {},
            "s3_key": self.s3_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class EvaluationRun(Base):
    """Tracked metrics and results for an AI evaluation run."""

    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    dataset_name: Mapped[str] = mapped_column(String(128), nullable=False)
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False)
    sql_accuracy: Mapped[float] = mapped_column(Float, nullable=False)
    execution_success: Mapped[float] = mapped_column(Float, nullable=False)
    answer_correctness: Mapped[float] = mapped_column(Float, nullable=False)
    groundedness: Mapped[float] = mapped_column(Float, nullable=False)
    avg_latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    repair_rate: Mapped[float] = mapped_column(Float, nullable=False)
    unresolved_rate: Mapped[float] = mapped_column(Float, nullable=False)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dataset_name": self.dataset_name,
            "total_cases": self.total_cases,
            "sql_accuracy": self.sql_accuracy,
            "execution_success": self.execution_success,
            "answer_correctness": self.answer_correctness,
            "groundedness": self.groundedness,
            "avg_latency_ms": self.avg_latency_ms,
            "repair_rate": self.repair_rate,
            "unresolved_rate": self.unresolved_rate,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
