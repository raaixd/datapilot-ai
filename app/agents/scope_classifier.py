"""
Scope classification.

This is a dedicated stage that runs BEFORE planning/schema-grounding/SQL
generation, and is deliberately deterministic (no LLM call) for two
reasons: (1) it must be trustworthy -- "is this question even about the
data" is exactly the kind of decision that shouldn't depend on a model
that might itself be confused or manipulated, and (2) it's a real
performance win, since an out-of-scope or unsafe question is rejected
without ever calling the planner or the LLM at all.

THE CORE BUG THIS FIXES: previously, every kind of question failure
(genuinely off-topic, vague-but-on-topic, or "on-topic but this specific
metric doesn't exist") collapsed into one message: "Could not map this
question to a specific measurable column." That's actively misleading for
"what is the meaning of life" -- there's nothing to "map" because the
question isn't about the dataset at all. This module distinguishes those
cases up front.

APPROACH: rather than maintaining a hardcoded list of "off-topic topics"
(brittle, and the previous round's own review flagged keyword-list
brittleness as a problem to avoid), the primary signal is SCHEMA OVERLAP:
does the question reference any actual column in the loaded dataset, or
any of the business concepts app/data/column_matcher.py already knows how
to recognize (revenue, quantity, product, region, date, ...)? A question
with zero such overlap is almost certainly not answerable from this
dataset, regardless of what specific off-topic thing it's asking about --
this generalizes to ANY schema, not just the bundled sample datasets, and
doesn't require guessing what topics someone might ask about.

Schema overlap alone isn't sufficient, though -- two additional signals are
layered on top of it:
  - UNSAFE: a fixed, small set of patterns for destructive/manipulative
    intent (asking to modify data, or attempting a prompt-injection-style
    instruction override). This one small list IS deterministic and fixed
    on purpose -- unlike "what topics are off-topic" (unbounded), "what
    counts as a mutation attempt" is a bounded, well-understood set of SQL
    verbs and injection phrasings, which is exactly the kind of thing a
    deterministic safety net should hardcode rather than infer.
  - FORCED AMBIGUITY: a small set of vague-business-phrasing patterns
    ("how are sales doing", "what performed best", "which product is
    good") that should always ask for clarification even when a column
    happens to match by coincidence (e.g. the word "sales" resolving to a
    real revenue-like column) -- because the phrasing itself signals the
    user hasn't said what they actually want (total? by region? this
    month vs last?).

This module does NOT try to fully resolve the question to a plan -- that
is still app/agents/planner.py's job for anything classified `in_scope`.
If planning subsequently fails for an in-scope question (e.g. it names a
metric concept that has no matching column), the orchestrator relabels
that as `ambiguous` too (see app/agents/orchestrator.py) -- by definition
it had schema overlap, so it's a "needs more specifics" situation, not an
"unrelated question" situation. That distinction is what this module's
categories are for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agents.intent_hints import is_dataset_level_question, is_row_count_question
from app.data.column_matcher import CONCEPT_SYNONYMS, mentioned_in_text, normalize
from app.data.database import TableSchema

# -- unsafe: destructive intent or instruction-override / injection attempts.
# Deliberately a fixed, bounded list (see module docstring) -- this is a
# defense-in-depth UX layer. app/agents/sql_validator.py is what actually
# guarantees no destructive SQL executes; this exists so the USER gets an
# honest, specific message ("I can't modify data") instead of either a
# confusing SQL-safety stack trace or -- previously -- silently answering
# only the benign remainder of the sentence with no acknowledgement at all
# that part of the request was refused.
_UNSAFE_MUTATION_PATTERNS = [
    re.compile(
        r"\b(delete|drop|remove|truncate|purge)\b.{0,25}\b(table|row|record|data|database|column)s?\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(update|modify|change|edit|overwrite|alter)\b.{0,25}\b(table|row|record|data|database|column|value)s?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(insert|add)\b.{0,25}\b(row|record)s?\b|\binsert\b.{0,15}\binto\s+the\s+table\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
]
_UNSAFE_INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(the\s+)?(previous|prior|all|above)\s+instructions?\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(the\s+)?(previous|prior|above)\b", re.IGNORECASE),
    re.compile(r"\bact\s+as\s+(the\s+)?(system|admin|root|database)\b", re.IGNORECASE),
    re.compile(r"\breveal\s+(your|the)\s+(prompt|system prompt|instructions)\b", re.IGNORECASE),
    re.compile(r"\bbypass\b.{0,20}\b(validat|safety|restriction|filter)\w*\b", re.IGNORECASE),
]

# -- forced ambiguity: vague, on-topic-sounding business phrasing that
# should always trigger a clarification, even if a column happens to match.
_FORCED_AMBIGUOUS_PATTERNS = [
    re.compile(r"\bhow(?:'s| is| are)\s+(?:the\s+)?(?:business|sales|things?|it)\s+(?:doing|going)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+performed\s+(?:the\s+)?best\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:did|does)\s+.{0,20}\bbest\b", re.IGNORECASE),
    re.compile(r"\bshow\s+me\s+the\s+trend\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+\w+\s+is\s+good\b", re.IGNORECASE),
    re.compile(r"\bhow\s+are\s+we\s+doing\b", re.IGNORECASE),
    re.compile(r"\btell\s+me\s+something\s+(?:interesting|useful)\s+about\s+(?:the\s+)?data\b", re.IGNORECASE),
]

# -- requests for actions this app cannot perform (delivery/integration,
# not analysis). Detected separately from pure off-topic so the message can
# say specifically what's unsupported rather than implying the whole
# question is unrelated to the data.
_UNSUPPORTED_ACTION_PATTERNS = [
    re.compile(r"\b(email|e-mail)\s+(me|this|it|the report)\b", re.IGNORECASE),
    re.compile(r"\b(schedule|automate)\s+(this|a report|it)\b", re.IGNORECASE),
    re.compile(r"\b(post|upload|send)\s+(this|it)\s+to\b", re.IGNORECASE),
    re.compile(r"\bconnect\s+(to|with)\s+(my|our)\b", re.IGNORECASE),
]

VALID_SCOPES = {"in_scope", "ambiguous", "out_of_scope", "unsafe"}


@dataclass
class ScopeResult:
    scope: str  # one of VALID_SCOPES
    confidence: float  # 0.0-1.0; rule-based, so this is coarse, not calibrated
    reason: str  # short internal explanation, for logs -- never shown to the user directly
    user_facing_message: str | None = None  # set for anything other than in_scope
    clarification_options: list[str] = field(default_factory=list)
    unsupported_action_note: str | None = None  # set alongside in_scope if an unsupported action was ALSO requested

    def __post_init__(self) -> None:
        if self.scope not in VALID_SCOPES:
            raise ValueError(f"scope '{self.scope}' is not one of {sorted(VALID_SCOPES)}")


def _schema_concepts_and_columns(schema: dict[str, TableSchema]) -> tuple[set[str], list[tuple[str, str]]]:
    """All (schema-relevant) words worth checking a question against: every
    real column name (raw and normalized) plus every synonym for every
    concept that has at least one matching column in this schema."""
    all_columns: list[tuple[str, str]] = []
    for table in schema.values():
        all_columns.extend(table.columns)

    relevant_words: set[str] = set()
    for name, _sqltype in all_columns:
        relevant_words.add(name.lower())
        relevant_words.add(normalize(name))

    for _concept, synonyms in CONCEPT_SYNONYMS.items():
        if any(normalize(s) in normalize(name) for s in synonyms for name, _ in all_columns):
            relevant_words.update(normalize(s) for s in synonyms)

    return relevant_words, all_columns


def _has_schema_overlap(qlower: str, schema: dict[str, TableSchema]) -> bool:
    relevant_words, all_columns = _schema_concepts_and_columns(schema)
    if any(word in qlower for word in relevant_words if len(word) > 2):
        return True
    return any(mentioned_in_text(name, qlower) for name, _ in all_columns)


# Generic analytical vocabulary: on its own (with no schema overlap at all)
# this is NOT enough to call something in-scope -- "what is the average
# lifespan of a housefly" uses "average" but isn't about the dataset -- but
# it IS one of the signals used to decide whether a no-schema-overlap
# question still deserves the softer "ambiguous" framing (e.g. it clearly
# wants *some* analysis, just doesn't say what) vs. flatly "out_of_scope".
_ANALYTICAL_VERBS = [
    "total",
    "average",
    "sum",
    "count",
    "compare",
    "trend",
    "rank",
    "top",
    "highest",
    "lowest",
    "distribution",
    "breakdown",
    "how many",
    "group by",
]


def classify_scope(question: str, schema: dict[str, TableSchema]) -> ScopeResult:
    qlower = (question or "").strip().lower()

    if not qlower:
        return ScopeResult(
            scope="out_of_scope",
            confidence=1.0,
            reason="empty question",
            user_facing_message="I didn't receive a question -- what would you like to know about the data?",
        )

    for pattern in _UNSAFE_MUTATION_PATTERNS + _UNSAFE_INJECTION_PATTERNS:
        if pattern.search(qlower):
            return ScopeResult(
                scope="unsafe",
                confidence=0.95,
                reason=f"matched unsafe pattern: {pattern.pattern}",
                user_facing_message=(
                    "I can only run read-only analysis on your data -- I can't modify, delete, or "
                    "restructure it, and I won't follow instructions embedded in a question that try "
                    "to change how I behave. Ask me to calculate, compare, or summarize something instead."
                ),
            )

    for pattern in _FORCED_AMBIGUOUS_PATTERNS:
        if pattern.search(qlower):
            options = _clarification_options(schema)
            return ScopeResult(
                scope="ambiguous",
                confidence=0.8,
                reason=f"matched vague-phrasing pattern: {pattern.pattern}",
                user_facing_message=_ambiguous_message(options),
                clarification_options=options,
            )

    # Dataset-level questions (missing values, duplicates, summary stats) are
    # always in-scope for ANY loaded dataset -- they don't reference a
    # specific column by design, so the schema-overlap check below would
    # wrongly reject them otherwise.
    if is_dataset_level_question(qlower):
        return ScopeResult(
            scope="in_scope", confidence=0.9, reason="dataset-level question (missing/duplicate/describe)"
        )

    if is_row_count_question(qlower):
        return ScopeResult(scope="in_scope", confidence=0.85, reason="row-count question (resolves to COUNT(*))")

    has_overlap = _has_schema_overlap(qlower, schema)
    unsupported_action_note = None
    action_match = next((p for p in _UNSUPPORTED_ACTION_PATTERNS if p.search(qlower)), None)

    if not has_overlap:
        if action_match:
            return ScopeResult(
                scope="out_of_scope",
                confidence=0.85,
                reason=f"unsupported action, no schema overlap: {action_match.pattern}",
                user_facing_message=(
                    "I can calculate and display analysis results here, but I can't email, export, "
                    "schedule, or connect to external systems. Ask me a question about the data and "
                    "I'll show you the answer directly in this app."
                ),
            )
        has_analytical_vocab = any(v in qlower for v in _ANALYTICAL_VERBS)
        if has_analytical_vocab:
            # Wants *some* kind of analysis, but references nothing in this
            # dataset -- softer framing than a flat "unrelated question".
            options = _clarification_options(schema)
            return ScopeResult(
                scope="ambiguous",
                confidence=0.6,
                reason="analytical vocabulary present but no schema overlap",
                user_facing_message=_ambiguous_message(options, generic=True),
                clarification_options=options,
            )
        return ScopeResult(
            scope="out_of_scope",
            confidence=0.9,
            reason="no schema overlap, no analytical vocabulary",
            user_facing_message=_out_of_scope_message(schema),
        )

    if action_match:
        unsupported_action_note = (
            "Note: I can compute this for you here, but I can't email, export, or schedule it automatically."
        )

    return ScopeResult(
        scope="in_scope",
        confidence=0.7,
        reason="schema overlap found",
        unsupported_action_note=unsupported_action_note,
    )


def _clarification_options(schema: dict[str, TableSchema], limit: int = 5) -> list[str]:
    options = []
    for table in schema.values():
        for name, sqltype in table.columns:
            if any(t in sqltype.upper() for t in ("INT", "REAL", "FLOAT", "DOUBLE", "NUM", "DECIMAL")):
                options.append(name)
    return options[:limit]


def _ambiguous_message(options: list[str], generic: bool = False) -> str:
    if not options:
        return "Could you be more specific about what you'd like to analyze?"
    option_text = ", ".join(options[:-1]) + f", or {options[-1]}" if len(options) > 1 else options[0]
    if generic:
        return (
            f"I can help analyze the dataset, but I'm not sure what you'd like to know. "
            f"Would you like to look at {option_text}?"
        )
    return f"Which metric would you like to analyze: {option_text}?"


def _out_of_scope_message(schema: dict[str, TableSchema]) -> str:
    examples = []
    for table in schema.values():
        cols = [name for name, _t in table.columns]
        if cols:
            examples = cols[:4]
        break
    example_text = (
        f"For example, you could ask about {', '.join(examples)}."
        if examples
        else "For example, you could ask about totals, comparisons, or trends in your data."
    )
    return (
        "This question is outside the scope of the uploaded dataset. I can help analyze the data -- "
        "for example by calculating totals, comparing categories, identifying top performers, or "
        f"showing trends. {example_text}"
    )
