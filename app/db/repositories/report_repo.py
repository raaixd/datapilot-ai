"""
Repository for ReportModel entities.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.models import ReportModel
from app.db.repositories.base import BaseRepository


class ReportRepository(BaseRepository):
    """Data access operations for analytical reports."""

    def create_report(
        self,
        project_id: str,
        dataset_id: str,
        title: str,
        summary: str | None = None,
        content: dict[str, Any] | None = None,
        s3_key: str | None = None,
        report_id: str | None = None,
    ) -> ReportModel:
        kwargs: dict[str, Any] = {
            "project_id": project_id,
            "dataset_id": dataset_id,
            "title": title.strip(),
            "summary": summary.strip() if summary else None,
            "content_json": content or {},
            "s3_key": s3_key,
        }
        if report_id:
            kwargs["id"] = report_id

        report = ReportModel(**kwargs)
        self.session.add(report)
        self.session.flush()
        return report

    def get_report(self, report_id: str) -> ReportModel | None:
        stmt = select(ReportModel).where(ReportModel.id == report_id)
        return self.session.scalars(stmt).first()

    def list_reports(
        self,
        project_id: str | None = None,
        dataset_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReportModel]:
        stmt = select(ReportModel)
        if project_id:
            stmt = stmt.where(ReportModel.project_id == project_id)
        if dataset_id:
            stmt = stmt.where(ReportModel.dataset_id == dataset_id)
        stmt = stmt.order_by(ReportModel.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def delete_report(self, report_id: str) -> bool:
        report = self.get_report(report_id)
        if not report:
            return False
        self.session.delete(report)
        self.session.flush()
        return True
