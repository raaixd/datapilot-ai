"""
Prompt templates.

Two prompts are used, corresponding to the two LLM calls in the workflow:

  1. PLANNER: turns a natural-language question + schema into a small,
     structured JSON "analysis plan" (intent, target table, metric,
     dimension, filters, chart type, follow-up questions). This is the
     "structured analysis plan" step required by the spec, and it is what
     the SQL generator is grounded on -- the LLM is never asked to jump
     straight from English to SQL with no intermediate structure.

  2. SQL_GENERATOR: turns the analysis plan + real schema into a single
     SQL SELECT statement. The model is only ever shown the actual table
     and column names that exist (schema-grounded), and is explicitly told
     it may only produce SELECT statements -- but per app/agents/sql_validator.py,
     that instruction is a hint, not the safety mechanism.

Each prompt embeds a `TASK:` marker consumed by the mock client
(app/llm/mock_client.py) so it can behave differently for each call
without any special-casing in the orchestrator.
"""
from __future__ import annotations

PLANNER_SYSTEM_PROMPT = """You are a business analytics planner. Given a user's
natural-language question and a database schema, produce ONLY a JSON object
(no prose, no markdown fences) with this exact shape:

{
  "intent": "aggregation" | "trend" | "ranking" | "comparison" | "distribution" | "unsupported",
  "table": "<one table name from the schema>",
  "metric_column": "<numeric column to aggregate, or null>",
  "aggregation": "sum" | "avg" | "count" | "min" | "max" | null,
  "dimension_column": "<column to group by, or null>",
  "date_column": "<column to use for a time trend, or null>",
  "filters": [{"column": "...", "op": "=", "value": "..."}],
  "chart_type": "bar" | "line" | "pie" | "scatter" | "table",
  "sort_desc": true | false,
  "clarification_needed": "<a question to ask the user, or null>"
}

Only reference tables/columns that actually exist in the provided schema.
Where a column's schema line lists "values: [...]", only use one of those
exact values in a filter -- never invent a value that isn't shown.
If a CONTEXT section is present, it contains retrieved business-term
definitions, the most relevant columns for this question, and validated
example patterns -- use it to disambiguate wording and inform column
choice, but the SCHEMA section remains the source of truth for what
actually exists.
If the question cannot be answered with the given schema, set
"intent" to "unsupported" and explain why in "clarification_needed".
"""

SQL_GENERATOR_SYSTEM_PROMPT = """You are a SQL generator for a read-only
analytics tool. Given a structured analysis plan and the real database
schema, produce ONLY a single SQL SELECT statement (no prose, no markdown
fences, no semicolon-separated statements). Only SELECT is permitted; you
must never produce INSERT, UPDATE, DELETE, DROP, or any other statement.
Only reference tables and columns that appear in the schema you are given.
"""

INSIGHT_SYSTEM_PROMPT = """You are a business analyst writing a short,
factual summary of query results. You are given the executed SQL, the
resulting rows, and computed metrics. Write 2-4 sentences that describe
ONLY what is present in that data. Never invent a number that is not in
the provided results. If the result set is empty, say so plainly instead
of guessing."""


def build_planner_user_prompt(question: str, schema_description: str, context_text: str = "") -> str:
    context_block = f"CONTEXT:\n{context_text}\n" if context_text else ""
    return f"TASK: plan\n{context_block}QUESTION: {question}\nSCHEMA:\n{schema_description}\n"


def build_sql_user_prompt(plan_json: str, schema_description: str, context_text: str = "") -> str:
    # CONTEXT must come BEFORE "PLAN:" -- app/llm/mock_client.py's SQL-step
    # parser captures everything between "PLAN:\n" and the next "\nSCHEMA:"
    # non-greedily; placing CONTEXT between those two markers would corrupt
    # that capture (and break json.loads on the plan). See
    # tests/test_rag_prompt_integration.py for a regression test of this.
    context_block = f"CONTEXT:\n{context_text}\n" if context_text else ""
    return f"TASK: sql\n{context_block}PLAN:\n{plan_json}\nSCHEMA:\n{schema_description}\n"


def build_insight_user_prompt(question: str, sql: str, result_preview: str, metrics_json: str) -> str:
    return (
        f"TASK: insight\nQUESTION: {question}\nSQL: {sql}\n"
        f"RESULT_PREVIEW:\n{result_preview}\nMETRICS:\n{metrics_json}\n"
    )
