"""
Deterministic mock LLM client.

This is NOT a toy stub that returns a fixed string. It parses the actual
schema (including a handful of real sample values for low-cardinality text
columns, and synonym-aware concept matching via app/data/column_matcher.py)
and applies keyword/phrase rules to the actual question text, so the rest
of the pipeline (planner -> validator -> executor -> analytics -> report)
can be exercised end-to-end, deterministically, with no network access and
no API key. This is what the automated test suite and the evaluation
harness (eval/run_eval.py) run against.

It is intentionally simple: it is a stand-in for an LLM, not a
reimplementation of one. Swapping LLM_PROVIDER to "anthropic" or "ollama"
replaces this with a real model; the rest of the app is unaware of the
difference (see app/llm/base.py). The system never claims to have used an
LLM when running in this mode -- see AnalysisResult.llm_provider, set from
config, and surfaced in the UI/API/reports.
"""

from __future__ import annotations

import json
import re

from app.agents.intent_hints import COUNT_HINTS, DESCRIPTIVE_STATS_HINTS, DUPLICATE_HINTS, MISSING_DATA_HINTS
from app.analytics.metrics import compute_metric_alias
from app.data.column_matcher import (
    extract_limit,
    find_columns_for_concept,
    is_singular_ranking_phrase,
    mentioned_in_text,
    normalize,
)
from app.llm.base import LLMClient

_TIME_HINTS = ["trend", "over time", "monthly", "month", "quarter", "quarterly", "weekly", "daily", "yearly"]
_RANK_HINTS = [
    "top",
    "highest",
    "lowest",
    "best",
    "worst",
    "rank",
    "ranking",
    "maximum",
    "minimum",
    "largest",
    "smallest",
    "most",
    "least",
]
_MIN_HINTS = ["lowest", "worst", "bottom", "smallest", "minimum", "least", "declining", "decreasing"]
_COMPARE_HINTS = ["compare", "versus", "vs", "difference between"]
_DIST_HINTS = ["distribution", "breakdown", "split", "by category", "segment"]
_PERCENT_CHANGE_HINTS = ["percentage change", "percent change", "% change", "growth rate", "growth over"]
_ANOMALY_HINTS = ["anomaly", "anomalies", "outlier", "outliers", "unusual value", "irregular value", "suspicious value"]
_DECLINE_HINTS = ["declining", "decreasing", "dropped", "falling", "which products experienced decline"]
_AMBIGUOUS_BEST_SELLING = ["best-selling", "best selling", "best seller", "most popular"]
_ASC_PHRASES = ["ascending order", "lowest to highest", "smallest to largest", "increasing order"]
_DESC_PHRASES = ["descending order", "highest to lowest", "largest to smallest", "decreasing order"]

_METRIC_CONCEPTS_PRIORITY = ["revenue", "quantity", "price", "profit"]


def _is_numeric_type(sqltype: str) -> bool:
    return any(t in sqltype.upper() for t in ["INT", "REAL", "FLOAT", "DOUBLE", "NUM", "DECIMAL"])


def _is_text_type(sqltype: str) -> bool:
    return any(t in sqltype.upper() for t in ["CHAR", "TEXT", "VARCHAR", "STRING", "OBJECT"])


def _parse_schema_block(schema_description: str) -> dict[str, dict]:
    """Parse the pipe-formatted schema text produced by
    app.agents.planner.describe_schema_text(). Returns, per table:
        {"columns": [(name, type), ...], "samples": {col_name: [value, ...]}}
    """
    tables: dict[str, dict] = {}
    current_table = None
    for line in schema_description.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("TABLE "):
            current_table = line[len("TABLE ") :].split(" ")[0].strip(":")
            tables[current_table] = {"columns": [], "samples": {}}
        elif line.startswith("- ") and current_table:
            m = re.match(r"-\s*(\w+)\s*\((\w+)\)(?:\s*values:\s*\[(.*)\])?", line)
            if m:
                col_name, col_type, values_blob = m.group(1), m.group(2), m.group(3)
                tables[current_table]["columns"].append((col_name, col_type))
                if values_blob:
                    tables[current_table]["samples"][col_name] = [
                        v.strip() for v in values_blob.split(",") if v.strip()
                    ]
    return tables


def _extract_filters(qlower: str, samples: dict[str, list[str]]) -> list[dict]:
    """Match real sample values against the question text, so a phrase like
    '...for the North region' becomes a WHERE filter on an actual known
    value -- never an invented one."""
    filters = []
    for column, values in samples.items():
        for value in values:
            if re.search(rf"\b{re.escape(value.lower())}\b", qlower):
                filters.append({"column": column, "op": "=", "value": value})
                break
    return filters


_INTERROGATIVE_STOPWORDS = {
    "which",
    "what",
    "who",
    "whom",
    "whose",
    "show",
    "list",
    "compare",
    "rank",
    "give",
    "find",
    "tell",
    "how",
    "are",
    "is",
    "the",
    "total",
    "average",
    "display",
}


def _detect_unrecognized_filter_reference(
    question: str, dimension_column: str | None, samples: dict[str, list[str]]
) -> str | None:
    """Catches a real correctness bug found via direct testing: a question
    like 'revenue for the Northwest region' (a value that doesn't exist in
    the data -- only North/South/East/West do) would otherwise silently
    drop the unrecognized filter and return the UNFILTERED total, with
    nothing telling the user their requested filter was ignored. This
    looks for a capitalized word positioned like a filter value (either
    immediately before the dimension's own name -- "Northwest region" --
    or right after "for"/"in"[, "the"] -- "for Northwest") and, if it
    doesn't match any of that column's known sampled values, returns it so
    the caller can ask for clarification instead of silently answering a
    different question than the one asked.

    Deliberately conservative: only runs when `dimension_column` was
    already resolved (so there's a concrete set of known values to check
    against) and that column's values were actually sampled (see
    app/data/database.py's column_samples -- capped at 30 distinct
    values, so a high-cardinality dimension isn't checked this way at
    all, which is a known/accepted gap for a rule-based mock -- see
    README "Known limitations").
    """
    if not dimension_column or dimension_column not in samples:
        return None
    known_values_lower = {v.lower() for v in samples[dimension_column]}

    dim_last_word = re.escape(normalize(dimension_column).split()[-1])
    candidate = None
    m = re.search(rf"\b([A-Z][a-zA-Z]*)\s+{dim_last_word}\b", question)
    if m and m.start() != 0:  # sentence-initial capitalization is just grammar, not a named value
        candidate = m.group(1)
    if candidate is None:
        m2 = re.search(r"\b(?:for|in)\s+(?:the\s+)?([A-Z][a-zA-Z]*)\b", question)
        if m2:
            candidate = m2.group(1)

    if (
        candidate
        and candidate.lower() not in _INTERROGATIVE_STOPWORDS
        and candidate.lower() not in known_values_lower
        and candidate.lower() not in normalize(dimension_column).split()
    ):
        return candidate
    return None


class MockLLMClient(LLMClient):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if "TASK: plan" in user_prompt:
            return self._plan(user_prompt)
        if "TASK: sql" in user_prompt:
            return self._sql(user_prompt)
        if "TASK: correct_sql" in user_prompt:
            return self._correct_sql(user_prompt)
        if "TASK: insight" in user_prompt:
            return self._insight(user_prompt)
        return ""

    def _correct_sql(self, user_prompt: str) -> str:
        sql_match = re.search(r"FAILING_SQL:\n(.*?)(?:\nERROR:|$)", user_prompt, re.DOTALL)
        err_match = re.search(r"ERROR:\n(.*?)(?:\nSCHEMA:|$)", user_prompt, re.DOTALL)
        schema_match = re.search(r"SCHEMA:\n([\s\S]*)", user_prompt)
        failing_sql = (sql_match.group(1) if sql_match else "").strip()
        error_msg = (err_match.group(1) if err_match else "").strip()
        schema_text = schema_match.group(1) if schema_match else ""
        tables = _parse_schema_block(schema_text)

        col_err = re.search(r"no such column:\s*([A-Za-z0-9_.]+)", error_msg, re.IGNORECASE)
        if col_err and tables:
            bad_col = col_err.group(1).split(".")[-1]
            all_cols = [c[0] for t in tables.values() for c in t["columns"]]
            if all_cols:
                target_col = all_cols[0]
                return failing_sql.replace(f'"{bad_col}"', f'"{target_col}"').replace(bad_col, target_col)

        tbl_err = re.search(r"no such table:\s*([A-Za-z0-9_.]+)", error_msg, re.IGNORECASE)
        if tbl_err and tables:
            bad_tbl = tbl_err.group(1)
            valid_tbl = next(iter(tables))
            return failing_sql.replace(f'"{bad_tbl}"', f'"{valid_tbl}"').replace(bad_tbl, valid_tbl)

        return failing_sql

    # -- planning -----------------------------------------------------
    def _plan(self, user_prompt: str) -> str:
        question_match = re.search(r"QUESTION:\s*(.*)", user_prompt)
        schema_match = re.search(r"SCHEMA:\n([\s\S]*)", user_prompt)
        question = (question_match.group(1) if question_match else "").strip()
        schema_text = schema_match.group(1) if schema_match else ""
        tables = _parse_schema_block(schema_text)

        def base_plan(**overrides) -> dict:
            plan = {
                "intent": "unsupported",
                "table": tables and table_name or None,
                "metric_column": None,
                "aggregation": None,
                "dimension_column": None,
                "date_column": None,
                "filters": [],
                "chart_type": "table",
                "sort_direction": None,
                "limit": None,
                "time_granularity": "month",
                "metric_alias": None,
                "ambiguous_options": [],
                "clarification_needed": None,
            }
            plan.update(overrides)
            return plan

        if not tables:
            return json.dumps(
                base_plan(
                    table=None,
                    clarification_needed="No dataset has been loaded yet.",
                )
            )

        qlower = question.lower()
        if len(tables) == 1:
            table_name = next(iter(tables))
        else:
            best_table = next(iter(tables))
            best_score = -1
            for t_name, t_data in tables.items():
                score = 0
                if t_name.lower() in qlower:
                    score += 5
                for col, _ in t_data["columns"]:
                    if col.lower() in qlower:
                        score += 3
                for smp_list in t_data["samples"].values():
                    for s in smp_list:
                        if s.lower() in qlower:
                            score += 2
                if score > best_score:
                    best_score = score
                    best_table = t_name
            table_name = best_table

        columns = tables[table_name]["columns"]
        samples = tables[table_name]["samples"]

        is_count_question = any(h in qlower for h in COUNT_HINTS)
        sort_phrase_hint = any(p in qlower for p in _ASC_PHRASES + _DESC_PHRASES)
        rank_hint = any(h in qlower for h in _RANK_HINTS) or sort_phrase_hint
        time_hint = any(h in qlower for h in _TIME_HINTS)
        compare_hint = any(h in qlower for h in _COMPARE_HINTS)
        dist_hint = any(h in qlower for h in _DIST_HINTS)
        percent_change_hint = any(h in qlower for h in _PERCENT_CHANGE_HINTS)
        decline_hint = any(h in qlower for h in _DECLINE_HINTS)
        anomaly_hint = any(h in qlower for h in _ANOMALY_HINTS)

        # -- meta intents answerable straight from the DataProfile, no SQL at all
        if any(h in qlower for h in MISSING_DATA_HINTS):
            return json.dumps(base_plan(intent="missing_data", chart_type="table"))
        if any(h in qlower for h in DUPLICATE_HINTS):
            return json.dumps(base_plan(intent="duplicate_analysis", chart_type="table"))
        if any(h in qlower for h in DESCRIPTIVE_STATS_HINTS):
            return json.dumps(base_plan(intent="descriptive_stats", chart_type="table"))

        # -- ambiguity: a phrase with more than one plausible metric interpretation
        if any(h in qlower for h in _AMBIGUOUS_BEST_SELLING):
            revenue_cols = find_columns_for_concept(columns, "revenue", _is_numeric_type)
            quantity_cols = find_columns_for_concept(columns, "quantity", _is_numeric_type)
            if revenue_cols and quantity_cols:
                return json.dumps(
                    base_plan(
                        ambiguous_options=[
                            f"total revenue ({revenue_cols[0]})",
                            f"units sold ({quantity_cols[0]})",
                        ],
                        clarification_needed=(
                            "This could mean the highest total revenue or the highest number of units sold. "
                            f"Do you mean ranking by '{revenue_cols[0]}' or by '{quantity_cols[0]}'?"
                        ),
                    )
                )
            # only one candidate metric exists, so there's nothing actually ambiguous here

        # -- column resolution, synonym-aware -------------------------------
        metric_column = self._resolve_metric_column(columns, qlower)
        dimension_column = self._resolve_dimension_column(columns, qlower)
        date_column = self._resolve_date_column(columns, qlower)
        filters = _extract_filters(qlower, samples)

        if not filters:
            unrecognized = _detect_unrecognized_filter_reference(question, dimension_column, samples)
            if unrecognized:
                known = ", ".join(samples.get(dimension_column, []))
                return json.dumps(
                    base_plan(
                        dimension_column=dimension_column,
                        clarification_needed=(
                            f"I don't see '{unrecognized}' as a value in the '{dimension_column}' column, so I "
                            f"can't filter by it. Known values include: {known}."
                        ),
                    )
                )

        if is_count_question:
            metric_column = "*"
        elif not metric_column:
            generic_agg_cue = (
                any(w in qlower for w in ["total", "sum", "average", "avg", "mean"]) or rank_hint or anomaly_hint
            )
            numeric_cols = [n for n, t in columns if _is_numeric_type(t)]
            if generic_agg_cue and len(numeric_cols) == 1:
                metric_column = numeric_cols[0]
            else:
                return json.dumps(
                    base_plan(
                        dimension_column=dimension_column,
                        date_column=date_column,
                        clarification_needed=(
                            f"I can see you're asking about '{table_name}', but I'm not sure which "
                            f"measurement you'd like -- could you specify one of: "
                            f"{', '.join(n for n, t in columns if _is_numeric_type(t))}?"
                        ),
                    )
                )

        # A resolved dimension_column already required either the literal
        # column name or a concept synonym to appear somewhere in the
        # question (see _resolve_dimension_column above) -- so once that's
        # true, a bare "by" anywhere in the question is a strong enough
        # signal of an intended group-by. (Previously this required an
        # exact "by <full column name>" phrase immediately adjacent, which
        # missed phrasing like "revenue by categories" against a column
        # literally named "product_category" -- the concept word alone,
        # not the full column name, is what appears in real questions.)
        implicit_groupby = dimension_column is not None and re.search(r"\bby\b", qlower)
        limit = extract_limit(qlower)
        singular = is_singular_ranking_phrase(qlower) and dimension_column is not None

        # -- intent classification -------------------------------------------
        if anomaly_hint:
            intent = "anomaly_detection"
        elif percent_change_hint:
            intent = "percentage_change"
        elif decline_hint and dimension_column and date_column:
            intent = "trend_by_dimension"
        elif decline_hint and date_column and not dimension_column:
            # "What's declining in sales?" with no named dimension: still
            # show the time trend (so the user can SEE the direction) rather
            # than collapsing to one flat total that says nothing about
            # decline at all.
            intent = "trend"
        elif time_hint and date_column and not dimension_column:
            intent = "trend"
        elif (rank_hint or singular) and dimension_column:
            intent = "ranking"
            if singular and limit is None:
                limit = 1
        elif compare_hint and dimension_column:
            intent = "grouped_comparison"
        elif dist_hint and dimension_column:
            intent = "grouped_comparison"
        elif implicit_groupby:
            intent = "grouped_comparison"
        else:
            intent = "aggregation"

        if is_count_question:
            aggregation = "count"
        elif intent == "aggregation" and rank_hint:
            aggregation = "min" if any(h in qlower for h in _MIN_HINTS) else "max"
        elif any(w in qlower for w in ["average", "avg", "mean"]):
            aggregation = "avg"
        else:
            aggregation = "sum"

        if any(p in qlower for p in _ASC_PHRASES):
            sort_direction = "asc"
        elif any(p in qlower for p in _DESC_PHRASES):
            sort_direction = "desc"
        elif any(h in qlower for h in _MIN_HINTS):
            sort_direction = "asc"
        else:
            sort_direction = "desc"

        chart_type = (
            "line"
            if intent in ("trend", "trend_by_dimension", "percentage_change")
            else "bar"
            if intent in ("ranking", "grouped_comparison")
            else "table"
        )
        metric_alias = compute_metric_alias(aggregation, metric_column)

        plan = base_plan(
            intent=intent,
            metric_column=metric_column,
            aggregation=aggregation,
            dimension_column=dimension_column,
            date_column=date_column,
            filters=filters,
            chart_type=chart_type,
            sort_direction=sort_direction,
            sort_desc=(sort_direction == "desc"),
            limit=limit,
            metric_alias=metric_alias,
        )
        return json.dumps(plan)

    def _resolve_metric_column(self, columns: list[tuple[str, str]], qlower: str) -> str | None:
        # 1. A numeric column literally (or via underscore/space normalization) named in the question.
        for name, sqltype in columns:
            if _is_numeric_type(sqltype) and mentioned_in_text(name, qlower):
                return name
        # 2. Synonym-based concept matching, tried in a fixed priority order, restricted
        #    to concepts actually referenced by some word in the question.
        for concept in _METRIC_CONCEPTS_PRIORITY:
            if not self._concept_hinted_in_question(concept, qlower):
                continue
            candidates = find_columns_for_concept(columns, concept, _is_numeric_type)
            if candidates:
                return candidates[0]
        return None

    @staticmethod
    def _concept_hinted_in_question(concept: str, qlower: str) -> bool:
        from app.data.column_matcher import CONCEPT_SYNONYMS, normalize

        norm_q = normalize(qlower)
        return any(syn in qlower or normalize(syn) in norm_q for syn in CONCEPT_SYNONYMS.get(concept, [concept]))

    def _resolve_dimension_column(self, columns: list[tuple[str, str]], qlower: str) -> str | None:
        for name, sqltype in columns:
            if _is_text_type(sqltype) and mentioned_in_text(name, qlower):
                return name
        for concept in ("product", "region", "category", "channel", "customer"):
            if not self._concept_hinted_in_question(concept, qlower):
                continue
            candidates = find_columns_for_concept(columns, concept, _is_text_type)
            if candidates:
                return candidates[0]
        return None

    def _resolve_date_column(self, columns: list[tuple[str, str]], qlower: str) -> str | None:
        candidates = find_columns_for_concept(columns, "date")
        if candidates:
            return candidates[0]
        return next((n for n, t in columns if "date" in n.lower() or "time" in n.lower()), None)

    # -- SQL generation -------------------------------------------------
    def _sql(self, user_prompt: str) -> str:
        plan_match = re.search(r"PLAN:\n([\s\S]*?)\nSCHEMA:", user_prompt)
        plan = json.loads(plan_match.group(1)) if plan_match else {}

        if plan.get("intent") in (
            "unsupported",
            "missing_data",
            "duplicate_analysis",
            "descriptive_stats",
        ) or not plan.get("table"):
            return "SELECT 1 WHERE 1=0"

        table = plan["table"]
        agg = (plan.get("aggregation") or "sum").upper()
        metric = plan.get("metric_column")
        dim = plan.get("dimension_column")
        date_col = plan.get("date_column")
        intent = plan.get("intent")
        limit = plan.get("limit")
        alias = plan.get("metric_alias") or "value"
        where_sql = _build_where_clause(plan.get("filters") or [])
        metric_expr = "*" if metric == "*" else f'"{metric}"'
        limit_sql = f" LIMIT {int(limit)}" if limit else ""

        if intent in ("trend", "percentage_change") and date_col and metric:
            return (
                f'SELECT strftime(\'%Y-%m\', "{date_col}") AS period, {agg}({metric_expr}) AS "{alias}" '
                f'FROM "{table}"{where_sql} GROUP BY period ORDER BY period{limit_sql}'
            )
        if intent == "trend_by_dimension" and date_col and dim and metric:
            return (
                f'SELECT "{dim}", strftime(\'%Y-%m\', "{date_col}") AS period, {agg}({metric_expr}) AS "{alias}" '
                f'FROM "{table}"{where_sql} GROUP BY "{dim}", period ORDER BY "{dim}", period'
            )
        if intent in ("ranking", "grouped_comparison") and dim and metric:
            order = "DESC" if plan.get("sort_direction", "desc") != "asc" else "ASC"
            return (
                f'SELECT "{dim}", {agg}({metric_expr}) AS "{alias}" FROM "{table}"{where_sql} '
                f'GROUP BY "{dim}" ORDER BY "{alias}" {order}{limit_sql}'
            )
        if metric:
            return f'SELECT {agg}({metric_expr}) AS "{alias}" FROM "{table}"{where_sql}{limit_sql}'
        return f'SELECT * FROM "{table}"{where_sql}{limit_sql}'

    # -- insight narration ------------------------------------------------
    def _insight(self, user_prompt: str) -> str:
        metrics_match = re.search(r"METRICS:\n([\s\S]*)", user_prompt)
        preview_match = re.search(r"RESULT_PREVIEW:\n([\s\S]*?)\nMETRICS:", user_prompt)
        preview = preview_match.group(1).strip() if preview_match else ""
        try:
            metrics = json.loads(metrics_match.group(1)) if metrics_match else {}
        except json.JSONDecodeError:
            metrics = {}

        if not preview or preview == "(no rows)":
            return "The query returned no rows, so no insight can be reported for this question."

        if metrics.get("no_data"):
            return "There is no data to compute this from -- the dataset (or the filtered subset) has zero rows."

        parts = []
        for key, val in metrics.items():
            if key in (
                "row_count",
                "column_count",
                "total",
                "average",
                "min",
                "max",
                "top_entry",
                "top_value",
                "trend_direction",
                "period_over_period_change_pct",
                "no_data",
            ):
                continue
            if val is None:
                continue
            if key not in ("declining_entities", "increasing_entities", "flat_entities", "entities_analyzed"):
                parts.append(f"The computed {key.replace('_', ' ')} is {val}.")
        if metrics.get("top_entry"):
            parts.append(f"The highest value belongs to '{metrics['top_entry']}'.")
        if metrics.get("declining_entities"):
            parts.append(f"Declining: {', '.join(metrics['declining_entities'])}.")
        if metrics.get("row_count") is not None and not parts:
            parts.append(f"The underlying query returned {metrics['row_count']} row(s).")
        if not parts:
            parts.append("Results were computed successfully from the executed SQL query.")
        return " ".join(parts)


def _build_where_clause(filters: list[dict]) -> str:
    clauses = []
    for f in filters:
        column, value = f.get("column"), f.get("value")
        if not column or value is None:
            continue
        escaped_value = str(value).replace("'", "''")
        clauses.append(f"\"{column}\" = '{escaped_value}'")
    return f" WHERE {' AND '.join(clauses)}" if clauses else ""
