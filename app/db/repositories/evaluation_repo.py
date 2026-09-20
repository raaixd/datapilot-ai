"""
Repository for EvaluationRun entities.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.models import EvaluationRun
from app.db.repositories.base import BaseRepository


class EvaluationRepository(BaseRepository):
    """Data access operations for AI benchmark evaluations."""

    def record_evaluation_run(
        self,
        dataset_name: str,
        total_cases: int,
        sql_accuracy: float,
        execution_success: float,
        answer_correctness: float,
        groundedness: float,
        avg_latency_ms: float,
        repair_rate: float,
        unresolved_rate: float,
        details: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> EvaluationRun:
        kwargs: dict[str, Any] = {
            "dataset_name": dataset_name,
            "total_cases": total_cases,
            "sql_accuracy": sql_accuracy,
            "execution_success": execution_success,
            "answer_correctness": answer_correctness,
            "groundedness": groundedness,
            "avg_latency_ms": avg_latency_ms,
            "repair_rate": repair_rate,
            "unresolved_rate": unresolved_rate,
            "details_json": details or {},
        }
        if run_id:
            kwargs["id"] = run_id

        eval_run = EvaluationRun(**kwargs)
        self.session.add(eval_run)
        self.session.flush()
        return eval_run

    def get_evaluation_run(self, run_id: str) -> EvaluationRun | None:
        stmt = select(EvaluationRun).where(EvaluationRun.id == run_id)
        return self.session.scalars(stmt).first()

    def list_evaluation_runs(self, limit: int = 20) -> list[EvaluationRun]:
        stmt = select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt).all())
