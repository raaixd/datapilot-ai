"""
HTTP-facing schemas.

Pydantic is used here, and only here -- the analysis core (profiler,
validator, planner, orchestrator) is pure dataclasses/stdlib so it has no
dependency on the web framework (see app/agents/planner.py's
PlanValidationError docstring for why: no network access to install
`pydantic` in this project's dev environment). This file requires
`pydantic` to import; see the NOTE in app/api/main.py about what has and
hasn't been executed in this sandbox.

`AnalysisPlanModel` mirrors app.agents.planner.AnalysisPlan field-for-field
with pydantic validators, so that when this API layer IS run with pydantic
installed, a plan crossing the HTTP boundary gets a second, independent
validation pass (on top of AnalysisPlan.validate(), which already runs
inside the tested core) -- belt and suspenders, not a replacement.
"""
from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.agents.planner import VALID_AGGREGATIONS, VALID_CHART_TYPES, VALID_INTENTS, VALID_SORT_DIRECTIONS


class QueryRequest(BaseModel):
    question: str
    dataset: str = "default"


class FilterOut(BaseModel):
    column: str
    op: str = "="
    value: str


class AnalysisPlanModel(BaseModel):
    """HTTP-facing mirror of app.agents.planner.AnalysisPlan."""
    intent: str
    table: str | None = None
    metric_column: str | None = None
    aggregation: str | None = None
    dimension_column: str | None = None
    date_column: str | None = None
    filters: list[FilterOut] = []
    chart_type: str = "table"
    sort_direction: str | None = None
    limit: int | None = None
    time_granularity: str = "month"
    metric_alias: str | None = None
    ambiguous_options: list[str] = []
    clarification_needed: str | None = None

    @field_validator("intent")
    @classmethod
    def _intent_must_be_known(cls, v: str) -> str:
        if v not in VALID_INTENTS:
            raise ValueError(f"intent '{v}' is not one of {sorted(VALID_INTENTS)}")
        return v

    @field_validator("aggregation")
    @classmethod
    def _aggregation_must_be_known(cls, v: str | None) -> str | None:
        if v not in VALID_AGGREGATIONS:
            raise ValueError(f"aggregation '{v}' is not one of {sorted(a for a in VALID_AGGREGATIONS if a)}")
        return v

    @field_validator("sort_direction")
    @classmethod
    def _sort_direction_must_be_known(cls, v: str | None) -> str | None:
        if v not in VALID_SORT_DIRECTIONS:
            raise ValueError("sort_direction must be 'asc', 'desc', or null")
        return v

    @field_validator("chart_type")
    @classmethod
    def _chart_type_must_be_known(cls, v: str) -> str:
        if v not in VALID_CHART_TYPES:
            raise ValueError(f"chart_type '{v}' is not one of {sorted(VALID_CHART_TYPES)}")
        return v

    @field_validator("limit")
    @classmethod
    def _limit_must_be_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("limit must be a positive integer")
        return v


class DataQualityWarningOut(BaseModel):
    column: str
    issue: str
    severity: str
    message: str
    affected_rows: int = 0


class QueryResponse(BaseModel):
    question: str
    success: bool
    sql: str | None = None
    insight: str | None = None
    metrics: dict = {}
    result_preview: list[dict] = []
    chart_type: str | None = None
    plan: AnalysisPlanModel | None = None
    follow_up_questions: list[str] = []
    data_quality_warnings: list[DataQualityWarningOut] = []
    error: str | None = None
    llm_provider: str | None = None  # e.g. "mock" -- never implies a live model ran when it didn't


class HealthResponse(BaseModel):
    status: str
    llm_provider: str
    database_backend: str
    tables_loaded: int
