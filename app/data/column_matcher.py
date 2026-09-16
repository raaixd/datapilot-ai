"""
Synonym-aware column matching.

Real datasets don't all name things "product", "revenue", "quantity", and
"date". This module maps a small set of common business *concepts* to the
alternate column names analysts actually use, and provides matching
functions that normalize case/underscores/spaces before comparing.

This is used by app/llm/mock_client.py (and is the module a real LLM
integration should also reference, so schema-matching behavior doesn't
drift between the deterministic mock and a live model).
"""

from __future__ import annotations

import re

# Concept -> substrings that indicate a column represents that concept.
# Order within each list does not imply priority; priority across concepts
# is handled by the caller (metric concepts are tried in a fixed order).
CONCEPT_SYNONYMS: dict[str, list[str]] = {
    "revenue": [
        "revenue",
        "sales_amount",
        "sales amount",
        "total_sales",
        "total sales",
        "sales",
        "amount",
        "order_value",
        "order value",
        "net_sales",
        "money",
        "earnings",
    ],
    "quantity": ["quantity", "units_sold", "units sold", "units", "qty", "item_count", "item count"],
    "price": ["unit_price", "unit price", "price", "cost"],
    "profit": ["profit", "margin"],
    "product": ["product_name", "product name", "product", "item_name", "item name", "item", "sku"],
    "region": ["region", "territory", "area", "market"],
    "category": ["category", "product_category", "product category", "segment", "department"],
    "channel": ["channel", "sales_channel", "sales channel", "source"],
    "date": [
        "order_date",
        "order date",
        "transaction_date",
        "transaction date",
        "purchase_date",
        "purchase date",
        "date",
        "timestamp",
    ],
    "customer": ["customer_id", "customer id", "customer", "client_id", "client"],
}


def _singularize_word(word: str) -> str:
    """Best-effort English singularization for MATCHING purposes only (never
    used for display text). Deliberately conservative: short words and
    words already ending in common non-plural '-us'/'-ss' patterns are left
    alone, since over-aggressive stripping causes worse false matches than
    under-stripping does."""
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def normalize(name: str) -> str:
    """Lowercase, turn underscores into spaces, AND singularize each word,
    so 'Product_Name'/'product name', and -- importantly -- 'category' /
    'categories' / 'product categories', all compare equal. This is applied
    consistently everywhere column/concept names are compared against
    question text (mentioned_in_text, column_matches_concept, and
    app/llm/mock_client.py's concept-hint checks all go through this one
    function), so a plural in the user's question can't silently fail to
    match a singular column name (or vice versa)."""
    cleaned = re.sub(r"[_\s]+", " ", name.strip().lower())
    return " ".join(_singularize_word(w) for w in cleaned.split(" ") if w)


def mentioned_in_text(name: str, text_lower: str) -> bool:
    """True if the column name (normalized/singularized, or raw) appears in
    free text -- text_lower is ALSO normalized before the substring check,
    so 'product categories' in the question matches a column literally
    named 'product_category'."""
    return name.lower() in text_lower or normalize(name) in normalize(text_lower)


def column_matches_concept(column_name: str, concept: str) -> bool:
    """True if `column_name` plausibly represents `concept` (e.g. a column
    named 'sales_amount' represents the 'revenue' concept)."""
    synonyms = CONCEPT_SYNONYMS.get(concept, [concept])
    norm_col = normalize(column_name)
    return any(normalize(s) in norm_col for s in synonyms)


def find_columns_for_concept(columns: list[tuple[str, str]], concept: str, type_filter=None) -> list[str]:
    """Return every column (in schema order) that plausibly represents a
    concept, optionally restricted by a type predicate (e.g. numeric-only).
    Returning *all* matches (not just the first) is what lets the caller
    detect genuine ambiguity -- e.g. two numeric columns that could both be
    "the" sales metric -- instead of silently picking one.
    """
    matches = []
    for name, sqltype in columns:
        if column_matches_concept(name, concept) and (type_filter is None or type_filter(sqltype)):
            matches.append(name)
    return matches


# Small English number-word map, enough for "top five products" / "first ten".
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
}


def extract_limit(text_lower: str) -> int | None:
    """Parse a result-size limit out of phrasing like 'top 5', 'top five',
    'first 10 products', 'the 3 best'. Returns None if no limit phrase is
    found (the caller should not assume 'no limit mentioned' means 1)."""
    digit_match = re.search(r"\b(?:top|first|last)\s+(\d+)\b", text_lower)
    if digit_match:
        return int(digit_match.group(1))

    word_match = re.search(r"\b(?:top|first|last)\s+(" + "|".join(_NUMBER_WORDS) + r")\b", text_lower)
    if word_match:
        return _NUMBER_WORDS[word_match.group(1)]

    bare_digit_match = re.search(r"\bthe\s+(\d+)\s+(?:best|worst|top|highest|lowest)\b", text_lower)
    if bare_digit_match:
        return int(bare_digit_match.group(1))

    return None


def is_singular_ranking_phrase(text_lower: str) -> bool:
    """True for 'which product has the highest revenue' / 'what product sold
    the most units' style phrasing, where the grammatical singular implies
    the caller wants exactly one answer (LIMIT 1) rather than a full ranked
    list."""
    return bool(re.search(r"\bwhich\s+\w+\s+(?:has|had|sold|made|generated)\b", text_lower)) or bool(
        re.search(r"\bwhat\s+\w+\s+(?:has|had|sold|made|generated)\b", text_lower)
    )
