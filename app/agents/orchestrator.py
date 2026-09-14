"""
Workflow orchestrator.

This is the single place that wires the pipeline together:

    question --> AnalysisPlanner --> SQLGenerator --> validate_sql
             --> AnalyticalDatabase.query --> compute_result_metrics
             --> insight narration --> AnalysisResult

Three intents (missing_data, duplicate_analysis, descriptive_stats) skip the
SQL path entirely and are answered directly from the already-computed
DataProfile -- there's nothing to query for those; the profiler already has
the answer, and generating SQL for them would be pure theater.

It never invents data: if planning fails, if SQL is rejected by the
validator, or if execution raises, the resulting AnalysisResult has
success=False and a clear error message instead of a fabricated answer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import pandas as pd

from app.agents.planner import AnalysisPlan, AnalysisPlanner
from app.agents.sql_generator import SQLGenerator
from app.agents.sql_validator import validate_sql
from app.analytics.metrics import compute_result_metrics
from app.core.config import Settings, get_settings
from app.data.database import AnalyticalDatabase
from app.data.profiler import DataProfile, DataQualityWarning
from app.llm.base import LLMClient
from app.llm.prompts import INSIGHT_SYSTEM_PROMPT, build_insight_user_prompt

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    question: str
    success: bool
    plan: AnalysisPlan | None = None
    sql: str | None = None
    result_preview: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    insight: str | None = None
    chart_type: str | None = None
    data_quality_warnings: list[DataQualityWarning] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    error: str | None = None
    validation_warnings: list[str] = field(default_factory=list)
    llm_provider: str | None = None  # e.g. "mock" -- never claim a real model answered when it didn't


class Orchestrator:
    def __init__(
        self, db: AnalyticalDatabase, llm_client: LLMClient, max_result_rows: int = 1000,
        settings: Settings | None = None,
    ):
        self._db = db
        self._planner = AnalysisPlanner(llm_client)
        self._sql_generator = SQLGenerator(llm_client)
        self._llm = llm_client
        self._max_result_rows = max_result_rows
        self._llm_provider = (settings or get_settings()).llm_provider

    def analyze(self, question: str, data_profile: DataProfile | None = None) -> AnalysisResult:
        question = (question or "").strip()
        logger.info("analyze() called: question=%r", question)
        if not question:
            return self._fail(question, "Question is empty.")

        schema = self._db.describe_schema()
        if not schema:
            return self._fail(question, "No dataset has been loaded. Upload a CSV or Excel file before asking a question.")

        plan = self._planner.plan(question, schema)
        warnings = list(data_profile.warnings) if data_profile else []

        if plan.intent == "unsupported" or plan.clarification_needed and not plan.is_answerable:
            logger.info("Question could not be answered: %s", plan.clarification_needed)
            return self._fail(
                question, plan.clarification_needed or "The question could not be mapped to the loaded dataset.",
                plan=plan, warnings=warnings,
            )

        if plan.is_profile_only:
            return self._answer_from_profile(question, plan, data_profile, warnings)

        if not plan.is_answerable:
            return self._fail(
                question, plan.clarification_needed or "The question could not be mapped to the loaded dataset.",
                plan=plan, warnings=warnings,
            )

        raw_sql = self._sql_generator.generate(plan, schema)
        validation = validate_sql(raw_sql, schema, max_result_rows=self._max_result_rows)
        if not validation.is_valid:
            logger.warning("Generated SQL failed validation: %s", validation.errors)
            return self._fail(
                question, "Generated SQL failed safety validation: " + "; ".join(validation.errors),
                plan=plan, sql=raw_sql, warnings=warnings,
            )

        try:
            result_df = self._db.query(validation.safe_sql, max_rows=self._max_result_rows)
        except Exception as exc:  # surfaced, never swallowed
            logger.exception("Query execution failed")
            return self._fail(
                question, f"Query execution failed: {exc}",
                plan=plan, sql=validation.safe_sql, warnings=warnings,
                validation_warnings=validation.warnings,
            )

        metric_alias = plan.metric_alias or "value"
        metrics = compute_result_metrics(result_df, metric_alias, plan.dimension_column)
        insight = self._narrate(question, validation.safe_sql, result_df, metrics)
        follow_ups = _default_follow_ups(plan)

        return AnalysisResult(
            question=question,
            success=True,
            plan=plan,
            sql=validation.safe_sql,
            result_preview=result_df.head(20).to_dict(orient="records"),
            metrics=metrics,
            insight=insight,
            chart_type=plan.chart_type,
            data_quality_warnings=warnings,
            follow_up_questions=follow_ups,
            validation_warnings=validation.warnings,
            llm_provider=self._llm_provider,
        )

    def _answer_from_profile(
        self, question: str, plan: AnalysisPlan, data_profile: DataProfile | None, warnings: list
    ) -> AnalysisResult:
        """Answer missing_data / duplicate_analysis / descriptive_stats directly
        from the DataProfile -- no SQL, no query execution. If no profile was
        supplied, this fails honestly rather than fabricating numbers."""
        if data_profile is None:
            return self._fail(
                question, "This question needs the dataset's data-quality profile, which hasn't been computed yet.",
                plan=plan, warnings=warnings,
            )

        if plan.intent == "missing_data":
            missing_cols = [c for c in data_profile.columns if c.null_count > 0]
            insight = (
                f"{len(missing_cols)} of {data_profile.column_count} column(s) have missing values."
                if missing_cols else "No missing values were found in any column."
            )
            metrics = {"columns_with_missing_values": len(missing_cols),
                       "total_missing_cells": sum(c.null_count for c in missing_cols)}
            preview = [{"column": c.name, "null_count": c.null_count, "null_pct": c.null_pct} for c in missing_cols]
        elif plan.intent == "duplicate_analysis":
            insight = (
                f"{data_profile.duplicate_row_count} duplicate row(s) found out of {data_profile.row_count}."
                if data_profile.duplicate_row_count else "No duplicate rows were found."
            )
            metrics = {"duplicate_row_count": data_profile.duplicate_row_count, "row_count": data_profile.row_count}
            preview = []
        else:  # descriptive_stats
            insight = f"'{data_profile.row_count}' rows across {data_profile.column_count} columns."
            metrics = {"row_count": data_profile.row_count, "column_count": data_profile.column_count}
            preview = [
                {"column": c.name, "type": c.inferred_type, "mean": c.mean, "min": c.min, "max": c.max,
                 "distinct_count": c.distinct_count}
                for c in data_profile.columns
            ]

        return AnalysisResult(
            question=question, success=True, plan=plan, sql=None,
            result_preview=preview, metrics=metrics, insight=insight,
            chart_type="table", data_quality_warnings=warnings,
            follow_up_questions=["Would you like to see this broken down by column or category?"],
            llm_provider=self._llm_provider,
        )

    def _fail(self, question, error, plan=None, sql=None, warnings=None, validation_warnings=None) -> AnalysisResult:
        return AnalysisResult(
            question=question, success=False, plan=plan, sql=sql, error=error,
            data_quality_warnings=warnings or [], validation_warnings=validation_warnings or [],
            llm_provider=self._llm_provider,
        )

    def _narrate(self, question: str, sql: str, result_df: pd.DataFrame, metrics: dict) -> str:
        preview = "(no rows)" if result_df.empty else result_df.head(10).to_string(index=False)
        user_prompt = build_insight_user_prompt(question, sql, preview, json.dumps(metrics))
        return self._llm.complete(INSIGHT_SYSTEM_PROMPT, user_prompt).strip()


def _default_follow_ups(plan: AnalysisPlan) -> list[str]:
    metric = plan.metric_column or "this metric"
    if plan.intent in ("trend", "trend_by_dimension", "percentage_change"):
        return [f"What drove the biggest month-over-month change in {metric}?",
                "How does this compare to the same period last year?"]
    if plan.intent == "ranking":
        return [f"What does the trend for the top entry's {metric} look like over time?",
                f"How concentrated is {metric} among the top few?"]
    if plan.intent == "grouped_comparison":
        return [f"Which of these groups grew the fastest in {metric}?"]
    if plan.intent == "aggregation":
        return [f"How does {metric} break down by category?", f"How has {metric} changed over time?"]
    return ["Would you like to see this broken down by another dimension?"]
