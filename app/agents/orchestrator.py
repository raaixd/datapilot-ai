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
from app.agents.scope_classifier import ScopeResult, classify_scope
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
    scope: str = "in_scope"  # "in_scope" | "ambiguous" | "out_of_scope" | "unsafe" -- see app/agents/scope_classifier.py
    clarification_options: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # e.g. "I can't email this" -- surfaced alongside a successful answer
    debug_info: str | None = None  # raw technical detail (exception text, validator errors) -- NEVER shown to
                                    # normal users; only surfaced by a caller when Settings.debug_mode is True


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
            return self._fail(question, "Question is empty.", scope="ambiguous")

        schema = self._db.describe_schema()
        if not schema:
            return self._fail(question, "No dataset has been loaded. Upload a CSV or Excel file before asking a question.", scope="out_of_scope")

        warnings = list(data_profile.warnings) if data_profile else []

        scope_result = classify_scope(question, schema)
        logger.info("Scope classification: scope=%s confidence=%.2f reason=%r", scope_result.scope, scope_result.confidence, scope_result.reason)

        if scope_result.scope == "unsafe":
            logger.warning("Unsafe question blocked before planning: %r", question)
            return self._fail(question, scope_result.user_facing_message, warnings=warnings, scope="unsafe")

        if scope_result.scope == "out_of_scope":
            return self._fail(question, scope_result.user_facing_message, warnings=warnings, scope="out_of_scope")

        if scope_result.scope == "ambiguous":
            return self._fail(
                question, scope_result.user_facing_message, warnings=warnings, scope="ambiguous",
                clarification_options=scope_result.clarification_options,
            )

        # scope_result.scope == "in_scope" from here on: this question has
        # genuine overlap with the loaded schema. Proceed to planning as
        # before, but if planning still can't resolve a concrete plan, that
        # failure is now known to be an "ambiguous" one (has schema overlap,
        # just not specific enough) rather than a generic/misleading error --
        # this is the fix for questions that mention dataset terms but the
        # planner still can't build an answerable plan from them.
        plan = self._planner.plan(question, schema)

        if plan.intent == "unsupported" or (plan.clarification_needed and not plan.is_answerable):
            logger.info("In-scope question could not be resolved to a plan: %s", plan.clarification_needed)
            options = plan.ambiguous_options or self._numeric_column_options(schema)
            return self._fail(
                question,
                plan.clarification_needed or "I need a bit more detail to answer that -- could you specify a metric to analyze?",
                plan=plan, warnings=warnings, scope="ambiguous", clarification_options=options,
            )

        if plan.is_profile_only:
            return self._answer_from_profile(question, plan, data_profile, warnings, scope_result)

        if plan.intent == "anomaly_detection":
            return self._answer_anomaly_detection(question, plan, schema, data_profile, warnings, scope_result)

        if not plan.is_answerable:
            return self._fail(
                question, plan.clarification_needed or "I need a bit more detail to answer that.",
                plan=plan, warnings=warnings, scope="ambiguous",
            )

        raw_sql = self._sql_generator.generate(plan, schema)
        validation = validate_sql(raw_sql, schema, max_result_rows=self._max_result_rows)
        if not validation.is_valid:
            logger.warning("Generated SQL failed validation: %s", validation.errors)
            return self._fail(
                question, "That question produced a query that didn't pass our safety checks, so I didn't run it. "
                           "Try rephrasing it more simply, or ask about a different metric.",
                plan=plan, sql=raw_sql, warnings=warnings, scope="in_scope",
                debug_info="SQL validation errors: " + "; ".join(validation.errors),
            )

        try:
            result_df = self._db.query(validation.safe_sql, max_rows=self._max_result_rows)
        except Exception as exc:  # surfaced, never swallowed
            logger.exception("Query execution failed")
            return self._fail(
                question, "I generated a query for that but it failed to run against your data. "
                           "This can happen with unusual column types or values -- try a simpler question.",
                plan=plan, sql=validation.safe_sql, warnings=warnings,
                validation_warnings=validation.warnings, scope="in_scope",
                debug_info=f"Query execution failed: {exc!r}",
            )

        metric_alias = plan.metric_alias or "value"
        metrics = compute_result_metrics(result_df, metric_alias, plan.dimension_column)
        insight = self._narrate(question, validation.safe_sql, result_df, metrics)
        follow_ups = _default_follow_ups(plan)
        notes = [scope_result.unsupported_action_note] if scope_result.unsupported_action_note else []

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
            scope="in_scope",
            notes=notes,
        )

    def _answer_from_profile(
        self, question: str, plan: AnalysisPlan, data_profile: DataProfile | None, warnings: list,
        scope_result: ScopeResult | None = None,
    ) -> AnalysisResult:
        """Answer missing_data / duplicate_analysis / descriptive_stats directly
        from the DataProfile -- no SQL, no query execution. If no profile was
        supplied, this fails honestly rather than fabricating numbers."""
        if data_profile is None:
            return self._fail(
                question, "This question needs the dataset's data-quality profile, which hasn't been computed yet.",
                plan=plan, warnings=warnings, scope="in_scope",
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

        notes = [scope_result.unsupported_action_note] if scope_result and scope_result.unsupported_action_note else []
        return AnalysisResult(
            question=question, success=True, plan=plan, sql=None,
            result_preview=preview, metrics=metrics, insight=insight,
            chart_type="table", data_quality_warnings=warnings,
            follow_up_questions=["Would you like to see this broken down by column or category?"],
            llm_provider=self._llm_provider, scope="in_scope", notes=notes,
        )

    def _answer_anomaly_detection(
        self, question: str, plan: AnalysisPlan, schema, data_profile: DataProfile | None, warnings: list,
        scope_result: ScopeResult | None = None, z_threshold: float = 2.0,
    ) -> AnalysisResult:
        """Flag rows whose metric value is more than `z_threshold` standard
        deviations from the column's mean.

        WHY THIS BYPASSES THE NORMAL SQL-GENERATION PATH: SQLite has no
        built-in STDDEV (and no guaranteed SQRT depending on build), so
        asking the LLM to write a self-contained "detect outliers" SQL
        query would either fail outright or require it to invent a mean/
        stddev approximation -- exactly the kind of number-fabrication this
        whole pipeline is built to avoid. The mean and standard deviation
        used here instead come from `app/data/profiler.py`'s ALREADY
        COMPUTED, ALREADY TESTED column statistics (real pandas math, done
        once at profiling time) -- they're substituted into the query as
        literal numbers, so the SQL itself is trivial (a single WHERE
        clause) and still goes through the normal `validate_sql()` safety
        check before running, same as every other query in this app.
        """
        if data_profile is None:
            return self._fail(
                question, "Detecting anomalies needs the dataset's data-quality profile, which hasn't been computed yet.",
                plan=plan, warnings=warnings, scope="in_scope",
            )

        column_profile = next((c for c in data_profile.columns if c.name == plan.metric_column), None)
        if column_profile is None or column_profile.mean is None or column_profile.std is None:
            return self._fail(
                question,
                f"I don't have enough statistics on '{plan.metric_column}' to detect anomalies in it "
                f"(it may be entirely empty, or not numeric).",
                plan=plan, warnings=warnings, scope="in_scope",
            )
        if column_profile.std == 0:
            return AnalysisResult(
                question=question, success=True, plan=plan, sql=None,
                metrics={"anomaly_count": 0, "mean": column_profile.mean, "std": 0},
                insight=f"Every value in '{plan.metric_column}' is the same ({column_profile.mean}), "
                        f"so there's no variation to detect anomalies against.",
                chart_type="table", data_quality_warnings=warnings, llm_provider=self._llm_provider, scope="in_scope",
            )

        sql = (
            f'SELECT * FROM "{plan.table}" WHERE ABS("{plan.metric_column}" - {column_profile.mean}) '
            f'> {z_threshold} * {column_profile.std} LIMIT {self._max_result_rows}'
        )
        validation = validate_sql(sql, schema, max_result_rows=self._max_result_rows)
        if not validation.is_valid:
            logger.error("Internally constructed anomaly-detection SQL failed validation: %s", validation.errors)
            return self._fail(
                question, "I couldn't safely construct an anomaly-detection query for that column.",
                plan=plan, warnings=warnings, scope="in_scope",
                debug_info="Anomaly SQL validation errors: " + "; ".join(validation.errors),
            )

        try:
            result_df = self._db.query(validation.safe_sql, max_rows=self._max_result_rows)
        except Exception as exc:
            logger.exception("Anomaly-detection query execution failed")
            return self._fail(
                question, "I tried to check for anomalies but the query failed to run against your data.",
                plan=plan, sql=validation.safe_sql, warnings=warnings, scope="in_scope",
                debug_info=f"Query execution failed: {exc!r}",
            )

        count = len(result_df)
        metrics = {
            "anomaly_count": count, "mean": column_profile.mean, "std": column_profile.std,
            "z_threshold": z_threshold, "row_count": data_profile.row_count,
        }
        insight = (
            f"No values in '{plan.metric_column}' fall more than {z_threshold:g} standard deviations from "
            f"the mean ({column_profile.mean:g}) -- nothing unusual detected."
            if count == 0 else
            f"Found {count} row(s) where '{plan.metric_column}' is more than {z_threshold:g} standard "
            f"deviations from the mean ({column_profile.mean:g}, std {column_profile.std:g})."
        )
        notes = [scope_result.unsupported_action_note] if scope_result and scope_result.unsupported_action_note else []

        return AnalysisResult(
            question=question, success=True, plan=plan, sql=validation.safe_sql,
            result_preview=result_df.head(20).to_dict(orient="records"), metrics=metrics, insight=insight,
            chart_type="table", data_quality_warnings=warnings,
            follow_up_questions=[f"What do these {plan.metric_column} outliers have in common?"],
            validation_warnings=validation.warnings, llm_provider=self._llm_provider, scope="in_scope", notes=notes,
        )


    def _fail(
        self, question, error, plan=None, sql=None, warnings=None, validation_warnings=None,
        scope: str = "out_of_scope", clarification_options: list[str] | None = None, debug_info: str | None = None,
    ) -> AnalysisResult:
        return AnalysisResult(
            question=question, success=False, plan=plan, sql=sql, error=error,
            data_quality_warnings=warnings or [], validation_warnings=validation_warnings or [],
            llm_provider=self._llm_provider, scope=scope,
            clarification_options=clarification_options or [], debug_info=debug_info,
        )

    def _numeric_column_options(self, schema) -> list[str]:
        options = []
        for table in schema.values():
            for name, sqltype in table.columns:
                if any(t in sqltype.upper() for t in ("INT", "REAL", "FLOAT", "DOUBLE", "NUM", "DECIMAL")):
                    options.append(name)
        return options[:5]

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
