from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.data.database import TableSchema
from app.llm.base import LLMClient
from app.llm.prompts import PLANNER_SYSTEM_PROMPT, build_planner_user_prompt
from app.rag.retriever import retrieve

VALID_INTENTS = {
    "aggregation", "ranking", "grouped_comparison", "trend", "trend_by_dimension",
    "percentage_change", "descriptive_stats", "missing_data", "duplicate_analysis",
    "anomaly_detection", "unsupported",
}
VALID_AGGREGATIONS = {"sum", "avg", "min", "max", "count", None}
VALID_SORT_DIRECTIONS = {"asc", "desc", None}
VALID_CHART_TYPES = {"bar", "line", "pie", "scatter", "table"}


class PlanValidationError(ValueError):
    """Raised when a plan (from the LLM or the mock client) doesn't satisfy
    the structural contract the rest of the pipeline depends on. This is a
    stdlib stand-in for what a pydantic validator would enforce -- there is
    no network access to install `pydantic` in this project's dev
    environment (see README, 'Structured plan validation'), so the same
    checks a pydantic model would run in `__init__` are written out
    explicitly here and run from `AnalysisPlan.validate()`."""


@dataclass
class AnalysisPlan:
    intent: str
    table: str | None
    metric_column: str | None
    aggregation: str | None
    dimension_column: str | None
    date_column: str | None
    filters: list[dict]
    chart_type: str
    clarification_needed: str | None
    sort_desc: bool = True                 # retained for backward compatibility; prefer sort_direction
    sort_direction: str | None = None       # "asc" | "desc"
    limit: int | None = None
    time_granularity: str = "month"
    metric_alias: str | None = None
    ambiguous_options: list[str] = field(default_factory=list)
    question: str = ""  # the original question this plan was built for (provenance, and used by
                         # SQLGenerator to re-run retrieval for the SQL-generation prompt)

    @property
    def is_answerable(self) -> bool:
        return self.intent != "unsupported" and self.table is not None

    @property
    def is_profile_only(self) -> bool:
        """True for intents answerable directly from the DataProfile,
        without generating or executing any SQL at all."""
        return self.intent in ("missing_data", "duplicate_analysis", "descriptive_stats")

    def validate(self) -> None:
        """Structural validation mirroring what a pydantic model would
        enforce in __init__. Raises PlanValidationError; never silently
        coerces a bad value into a guess."""
        errors = []
        if self.intent not in VALID_INTENTS:
            errors.append(f"intent '{self.intent}' is not one of {sorted(VALID_INTENTS)}")
        if self.aggregation not in VALID_AGGREGATIONS:
            errors.append(f"aggregation '{self.aggregation}' is not one of {sorted(a for a in VALID_AGGREGATIONS if a)}")
        if self.sort_direction not in VALID_SORT_DIRECTIONS:
            errors.append(f"sort_direction '{self.sort_direction}' must be 'asc', 'desc', or null")
        if self.chart_type not in VALID_CHART_TYPES:
            errors.append(f"chart_type '{self.chart_type}' is not one of {sorted(VALID_CHART_TYPES)}")
        if self.limit is not None and (not isinstance(self.limit, int) or self.limit <= 0):
            errors.append(f"limit must be a positive integer, got {self.limit!r}")
        if self.is_answerable and not self.is_profile_only and self.metric_column is None:
            errors.append("an answerable, non-profile-only plan must set metric_column")
        if errors:
            raise PlanValidationError("; ".join(errors))


def describe_schema_text(schema: dict[str, TableSchema]) -> str:
    """Render the schema in the compact pipe format the mock client (and the
    real-LLM prompt) both parse. Kept as plain text, not JSON, to keep the
    prompt short and cheap."""
    lines = []
    for table in schema.values():
        lines.append(f"TABLE {table.name}: ({table.row_count} rows)")
        for col_name, col_type in table.columns:
            samples = table.column_samples.get(col_name) if table.column_samples else None
            if samples:
                lines.append(f"- {col_name} ({col_type}) values: [{', '.join(samples)}]")
            else:
                lines.append(f"- {col_name} ({col_type})")
    return "\n".join(lines) if lines else "(no tables loaded)"


class AnalysisPlanner:
    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    def plan(self, question: str, schema: dict[str, TableSchema]) -> AnalysisPlan:
        schema_text = describe_schema_text(schema)
        retrieved = retrieve(question, schema)
        user_prompt = build_planner_user_prompt(question, schema_text, retrieved.to_prompt_text())
        raw = self._llm.complete(PLANNER_SYSTEM_PROMPT, user_prompt)
        try:
            data = json.loads(_strip_code_fence(raw))
        except (json.JSONDecodeError, TypeError):
            return AnalysisPlan(
                intent="unsupported", table=None, metric_column=None, aggregation=None,
                dimension_column=None, date_column=None, filters=[], chart_type="table",
                clarification_needed="The planning step returned a response that could not be parsed as JSON.",
                question=question,
            )

        plan = AnalysisPlan(
            intent=data.get("intent", "unsupported"),
            table=data.get("table"),
            metric_column=data.get("metric_column"),
            aggregation=data.get("aggregation"),
            dimension_column=data.get("dimension_column"),
            date_column=data.get("date_column"),
            filters=data.get("filters") or [],
            chart_type=data.get("chart_type", "table"),
            clarification_needed=data.get("clarification_needed"),
            sort_desc=data.get("sort_desc", True),
            sort_direction=data.get("sort_direction"),
            limit=data.get("limit"),
            time_granularity=data.get("time_granularity", "month"),
            metric_alias=data.get("metric_alias"),
            ambiguous_options=data.get("ambiguous_options") or [],
            question=question,
        )

        try:
            plan.validate()
        except PlanValidationError as exc:
            return AnalysisPlan(
                intent="unsupported", table=plan.table, metric_column=None, aggregation=None,
                dimension_column=plan.dimension_column, date_column=plan.date_column, filters=[],
                chart_type="table",
                clarification_needed=f"The analysis plan failed validation and was discarded: {exc}",
                question=question,
            )
        return plan


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:] if lines and lines[0].startswith("```") else lines
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text
