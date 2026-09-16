"""
Business glossary and validated-example knowledge base.

This is the "context" side of the retrieval step: a small, structured,
hand-curated store of (a) business-term definitions and (b) validated
question -> query-strategy examples, keyed by the same intent/concept
vocabulary the rest of the app already uses (app/data/column_matcher.py's
CONCEPT_SYNONYMS, app/agents/planner.py's VALID_INTENTS). Nothing here is a
vector store or embedding index -- this is deliberately the "structured
metadata / keyword-based retrieval" starting point called for over a vector
store, which would be unjustified infrastructure for a knowledge base this
small. See app/rag/retriever.py for how this gets matched against a
question and turned into prompt context.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GlossaryEntry:
    term: str
    concept: str  # maps to app.data.column_matcher.CONCEPT_SYNONYMS keys
    definition: str


@dataclass
class ValidatedExample:
    """A validated question -> query-strategy pair, used as retrieved
    few-shot guidance for the planner/SQL-generation prompts. `sql_pattern`
    is illustrative (with placeholder table/column names), not something
    executed directly -- the actual SQL is still generated fresh, against
    the real loaded schema, and still passes through
    app/agents/sql_validator.py regardless of what this suggests."""

    intent: str
    question_pattern: str
    sql_pattern: str
    guidance: str


BUSINESS_GLOSSARY: list[GlossaryEntry] = [
    GlossaryEntry(
        "revenue",
        "revenue",
        "The total monetary amount generated from sales, before costs are subtracted. "
        "Often stored as 'revenue', 'sales_amount', 'total_sales', or 'amount'.",
    ),
    GlossaryEntry(
        "sales",
        "revenue",
        "Commonly used interchangeably with revenue in casual business language, though in some "
        "datasets 'sales' may refer to a count of transactions rather than a dollar amount -- "
        "check the actual column's data type before assuming which.",
    ),
    GlossaryEntry(
        "profit",
        "profit",
        "Revenue minus costs. Requires both a revenue-like AND a cost-like column to compute; "
        "if the dataset only has revenue, profit cannot be calculated from it alone.",
    ),
    GlossaryEntry(
        "margin",
        "profit",
        "Profit expressed as a percentage of revenue (profit / revenue * 100). Same data requirement as profit.",
    ),
    GlossaryEntry(
        "quantity",
        "quantity",
        "A count of units, items, or orders -- not a monetary amount. Often stored as "
        "'quantity', 'units_sold', or 'qty'.",
    ),
    GlossaryEntry(
        "order",
        "quantity",
        "A single transaction/purchase event. 'How many orders' usually means a row count "
        "(COUNT(*) or COUNT(order_id)), not a sum of any numeric column.",
    ),
    GlossaryEntry(
        "churn",
        "customer",
        "The rate at which customers stop purchasing. Requires a customer identifier AND a "
        "date/time column to compute over a period; cannot be answered from a single snapshot.",
    ),
    GlossaryEntry(
        "trend",
        "date",
        "A pattern over time. Requires a date/time column; without one, 'trend' cannot be "
        "computed and the question should be treated as needing more information.",
    ),
    GlossaryEntry(
        "decline",
        "date",
        "A decrease in a metric across two or more time periods. Requires both a date column "
        "and comparing at least two periods -- a single total for all time cannot show decline.",
    ),
]


VALIDATED_EXAMPLES: list[ValidatedExample] = [
    ValidatedExample(
        intent="ranking",
        question_pattern="Which <dimension> has the highest <metric>?",
        sql_pattern='SELECT "<dimension>", SUM("<metric>") AS total FROM "<table>" '
        'GROUP BY "<dimension>" ORDER BY total DESC LIMIT 1',
        guidance="A 'which X has the highest Y' question is a GROUP BY + ORDER BY + LIMIT 1 query, "
        "never a COUNT of an unrelated identifier column.",
    ),
    ValidatedExample(
        intent="trend_by_dimension",
        question_pattern="Which <dimension> experienced declining <metric>?",
        sql_pattern='SELECT "<dimension>", strftime(\'%Y-%m\', "<date_column>") AS period, '
        'SUM("<metric>") AS total FROM "<table>" GROUP BY "<dimension>", period ORDER BY "<dimension>", period',
        guidance="Answering 'declining' requires grouping by BOTH the dimension AND a time period, then "
        "comparing each dimension's values across periods -- a single flat total across all time "
        "cannot show an increase or decrease, and neither can a query that ignores the dimension "
        "entirely (e.g. a bare row count).",
    ),
    ValidatedExample(
        intent="trend",
        question_pattern="Show <metric> over time / monthly <metric>",
        sql_pattern='SELECT strftime(\'%Y-%m\', "<date_column>") AS period, SUM("<metric>") AS total '
        'FROM "<table>" GROUP BY period ORDER BY period',
        guidance="A time trend groups by a truncated date period, ordered chronologically -- never a "
        "single aggregate with no GROUP BY.",
    ),
    ValidatedExample(
        intent="aggregation",
        question_pattern="How many <records> are there?",
        sql_pattern='SELECT COUNT(*) AS total FROM "<table>"',
        guidance="A row-count question needs no specific metric column at all -- COUNT(*) answers it "
        "directly; don't force it to reference an unrelated numeric column.",
    ),
]


def glossary_entries_for_concepts(concepts: set[str]) -> list[GlossaryEntry]:
    return [e for e in BUSINESS_GLOSSARY if e.concept in concepts]


def examples_for_intent(intent: str, limit: int = 2) -> list[ValidatedExample]:
    return [e for e in VALIDATED_EXAMPLES if e.intent == intent][:limit]
