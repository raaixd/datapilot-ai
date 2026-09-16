"""
Shared phrase-hint constants for dataset-level (not column-level) question
types: missing-data, duplicate-row, and descriptive-statistics questions.

These are intentionally centralized rather than duplicated in both
app/agents/scope_classifier.py and app/llm/mock_client.py: both need to
recognize the same phrases (the scope classifier needs to know these are
in-scope even with zero column-name overlap, since "is there missing data?"
doesn't name any specific column; the mock LLM needs the same list to
actually route to the right intent). A single shared list means the two
can't silently drift apart -- if a phrase is added here, both recognize it.
"""
from __future__ import annotations

MISSING_DATA_HINTS = ["missing value", "missing data", "null value", "how complete", "data quality", "data is missing"]
DUPLICATE_HINTS = ["duplicate", "duplicated", "repeated rows", "same row twice"]
DESCRIPTIVE_STATS_HINTS = ["describe the data", "summary statistics", "basic statistics", "descriptive statistics",
                           "summarize the data", "overview of the data"]
COUNT_HINTS = ["how many", "count", "number of"]
# Generic nouns referring to "rows in this dataset" rather than some
# unrelated real-world countable thing -- deliberately bounded so "how many
# countries are there" (a general-knowledge question, not about the loaded
# data) is NOT swept into scope just because it contains "how many".
_RECORD_NOUNS = ["rows", "records", "entries", "orders", "transactions", "items", "results", "cases", "observations"]


def is_dataset_level_question(qlower: str) -> bool:
    """True for any question about the dataset as a whole (completeness,
    duplicates, summary stats) that doesn't need to reference a specific
    column to be perfectly valid and in-scope."""
    all_hints = MISSING_DATA_HINTS + DUPLICATE_HINTS + DESCRIPTIVE_STATS_HINTS
    return any(h in qlower for h in all_hints)


def is_row_count_question(qlower: str) -> bool:
    """True for 'how many orders/rows/records are there' style questions,
    which resolve to COUNT(*) and don't need to name a specific measurable
    column. Requires BOTH a count phrase AND a generic dataset-record noun,
    so a genuinely unrelated question like 'how many countries are there'
    is not swept into scope just because it contains 'how many'."""
    return any(h in qlower for h in COUNT_HINTS) and any(n in qlower for n in _RECORD_NOUNS)
