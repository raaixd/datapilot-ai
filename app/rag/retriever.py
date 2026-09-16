"""
Retrieval step: given a question and the loaded schema, retrieve the
subset of context actually relevant to answering it, and format it for
injection into the LLM prompt (see app/llm/prompts.py's CONTEXT section).

This is intentionally "structured metadata + keyword matching", not a
vector store: the knowledge base (app/rag/knowledge_base.py) is small and
hand-curated, and keyword/concept matching against it is both fully
deterministic (testable without a model) and sufficient at this scale.
Swapping in embeddings would add real infrastructure (an embedding model,
a vector index, another moving part to keep in sync with the schema) for a
knowledge base of a few dozen entries -- not justified yet. See README
"RAG / retrieval layer" for the explicit tradeoff writeup and what would
justify revisiting this (a much larger glossary, or example library, where
keyword matching starts missing relevant entries semantically-related but
lexically different).

WHAT THIS ACTUALLY FEEDS: app/agents/planner.py's AnalysisPlanner and
app/agents/sql_generator.py's SQLGenerator both call retrieve() and pass
the resulting context into the SAME prompt sent to the LLM (mock or real)
-- this is a real retrieval step wired into generation, not a label. See
tests/test_rag_retriever.py for retrieval-relevance tests, and
tests/test_rag_prompt_integration.py for proof the retrieved text actually
appears in what gets sent to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.data.column_matcher import CONCEPT_SYNONYMS, mentioned_in_text, normalize
from app.data.database import TableSchema
from app.rag.knowledge_base import (
    GlossaryEntry,
    ValidatedExample,
    examples_for_intent,
    glossary_entries_for_concepts,
)

# Lightweight, LOCAL intent hints for retrieval purposes only. Deliberately
# decoupled from app/llm/mock_client.py's classification hints: retrieval
# only needs to guess PLAUSIBLE relevant examples to surface, not commit to
# a final intent -- the actual classification (mock or real LLM) still
# happens downstream, independently, and isn't influenced by a wrong guess
# here beyond "maybe the wrong extra example was retrieved."
_RETRIEVAL_INTENT_HINTS = {
    "ranking": ["highest", "top", "best", "most", "rank", "lowest", "least"],
    "trend_by_dimension": ["declining", "decreasing", "falling", "dropped", "growing"],
    "trend": ["trend", "over time", "monthly", "quarterly", "weekly", "growth"],
    "aggregation": ["how many", "total", "count", "average", "sum"],
    "grouped_comparison": ["compare", "by region", "by category", "breakdown", "versus"],
}


@dataclass
class RetrievedContext:
    glossary_entries: list[GlossaryEntry] = field(default_factory=list)
    relevant_columns: list[tuple[str, str, list[str]]] = field(default_factory=list)  # (name, type, samples)
    examples: list[ValidatedExample] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.glossary_entries or self.relevant_columns or self.examples)

    def to_prompt_text(self) -> str:
        if self.is_empty:
            return ""
        lines = []
        if self.glossary_entries:
            lines.append("Relevant business term definitions:")
            for e in self.glossary_entries:
                lines.append(f"- {e.term}: {e.definition}")
        if self.relevant_columns:
            lines.append("Columns most relevant to this question:")
            for name, sqltype, samples in self.relevant_columns:
                sample_text = f" e.g. [{', '.join(samples)}]" if samples else ""
                lines.append(f"- {name} ({sqltype}){sample_text}")
        if self.examples:
            lines.append("Similar validated question patterns:")
            for ex in self.examples:
                lines.append(f'- "{ex.question_pattern}" -> {ex.guidance}')
        return "\n".join(lines)


def _guess_relevant_intents(qlower: str) -> list[str]:
    return [intent for intent, hints in _RETRIEVAL_INTENT_HINTS.items() if any(h in qlower for h in hints)]


def _relevant_concepts(qlower: str) -> set[str]:
    norm_q = normalize(qlower)
    concepts = set()
    for concept, synonyms in CONCEPT_SYNONYMS.items():
        if any(syn in qlower or normalize(syn) in norm_q for syn in synonyms):
            concepts.add(concept)
    return concepts


def _direct_glossary_matches(qlower: str) -> list:
    """Glossary entries whose TERM itself is mentioned in the question,
    independent of concept-synonym overlap. Some glossary terms ('order',
    'churn', 'decline') are curated precisely because they're words users
    say that aren't necessarily registered as column-matching synonyms
    anywhere -- concept overlap alone would miss them."""
    from app.rag.knowledge_base import BUSINESS_GLOSSARY

    norm_q = normalize(qlower)
    return [e for e in BUSINESS_GLOSSARY if normalize(e.term) in norm_q]


def retrieve(
    question: str, schema: dict[str, TableSchema], max_columns: int = 6, max_examples: int = 2
) -> RetrievedContext:
    """The retrieval step. Pure function of (question, schema) -- no
    network access, no LLM call, fully deterministic and unit-testable."""
    qlower = (question or "").strip().lower()
    if not qlower or not schema:
        return RetrievedContext()

    concepts = _relevant_concepts(qlower)
    glossary_entries = glossary_entries_for_concepts(concepts)
    for entry in _direct_glossary_matches(qlower):
        if entry not in glossary_entries:
            glossary_entries.append(entry)

    relevant_columns: list[tuple[str, str, list[str]]] = []
    for table in schema.values():
        for name, sqltype in table.columns:
            if mentioned_in_text(name, qlower) or any(
                normalize(syn) in normalize(name) for concept in concepts for syn in CONCEPT_SYNONYMS.get(concept, [])
            ):
                samples = table.column_samples.get(name, [])[:5] if table.column_samples else []
                relevant_columns.append((name, sqltype, samples))
    relevant_columns = relevant_columns[:max_columns]

    examples: list[ValidatedExample] = []
    for intent in _guess_relevant_intents(qlower):
        examples.extend(examples_for_intent(intent, limit=max_examples))
    examples = examples[:max_examples]

    return RetrievedContext(glossary_entries=glossary_entries, relevant_columns=relevant_columns, examples=examples)
