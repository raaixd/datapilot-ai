"""
Tests for the retrieval step (app/rag/retriever.py + knowledge_base.py).

These test RETRIEVAL RELEVANCE specifically -- given a question and a real
schema, does the retriever surface the columns/definitions/examples that
are actually relevant, and leave out ones that aren't? See
tests/test_rag_prompt_integration.py for proof the retrieved text actually
reaches the LLM prompt (a different, equally important thing to verify).
"""

import unittest

import pandas as pd

from app.data.database import AnalyticalDatabase
from app.rag.knowledge_base import (
    BUSINESS_GLOSSARY,
    VALIDATED_EXAMPLES,
    examples_for_intent,
    glossary_entries_for_concepts,
)
from app.rag.retriever import retrieve


def _sales_schema():
    df = pd.DataFrame(
        {
            "order_date": ["2024-01-01"],
            "region": ["North"],
            "product_category": ["Electronics"],
            "quantity": [1],
            "unit_price": [10.0],
            "revenue": [10.0],
        }
    )
    db = AnalyticalDatabase(backend="sqlite", path=":memory:")
    db.load_dataframe(df, "sales")
    return db.describe_schema()


class TestKnowledgeBase(unittest.TestCase):
    def test_glossary_has_entries(self):
        self.assertGreater(len(BUSINESS_GLOSSARY), 5)

    def test_glossary_entries_for_concepts_filters_correctly(self):
        entries = glossary_entries_for_concepts({"revenue"})
        self.assertTrue(all(e.concept == "revenue" for e in entries))
        self.assertTrue(any(e.term == "revenue" for e in entries))

    def test_examples_for_intent_filters_correctly(self):
        examples = examples_for_intent("ranking")
        self.assertTrue(all(e.intent == "ranking" for e in examples))

    def test_every_example_has_nonempty_guidance(self):
        for ex in VALIDATED_EXAMPLES:
            self.assertTrue(ex.guidance.strip())


class TestRetrievalRelevance(unittest.TestCase):
    def setUp(self):
        self.schema = _sales_schema()

    def test_revenue_question_retrieves_revenue_glossary_and_column(self):
        result = retrieve("What is the total revenue?", self.schema)
        self.assertTrue(any(e.term == "revenue" for e in result.glossary_entries))
        self.assertTrue(any(name == "revenue" for name, _t, _s in result.relevant_columns))

    def test_unrelated_column_not_retrieved(self):
        result = retrieve("What is the total revenue?", self.schema)
        retrieved_names = {name for name, _t, _s in result.relevant_columns}
        self.assertNotIn("region", retrieved_names)  # question doesn't mention region at all

    def test_declining_question_retrieves_trend_by_dimension_example(self):
        result = retrieve("Which products experienced declining sales?", self.schema)
        self.assertTrue(any(e.intent == "trend_by_dimension" for e in result.examples))

    def test_ranking_question_retrieves_ranking_example(self):
        result = retrieve("Which region has the highest revenue?", self.schema)
        self.assertTrue(any(e.intent == "ranking" for e in result.examples))

    def test_row_count_question_retrieves_order_glossary_entry(self):
        result = retrieve("How many orders are there?", self.schema)
        self.assertTrue(any(e.term == "order" for e in result.glossary_entries))

    def test_empty_question_retrieves_nothing(self):
        result = retrieve("", self.schema)
        self.assertTrue(result.is_empty)

    def test_no_schema_retrieves_nothing(self):
        result = retrieve("What is the total revenue?", {})
        self.assertTrue(result.is_empty)

    def test_off_topic_question_retrieves_little_or_nothing(self):
        result = retrieve("What is the meaning of life?", self.schema)
        self.assertEqual(result.relevant_columns, [])

    def test_column_samples_included_when_available(self):
        result = retrieve("What is the total revenue by region?", self.schema)
        region_entries = [c for c in result.relevant_columns if c[0] == "region"]
        self.assertTrue(region_entries)
        # sample values ARE populated by AnalyticalDatabase.describe_table for
        # low-cardinality text columns -- confirm they flow through retrieval.
        self.assertTrue(region_entries[0][2])  # non-empty samples list


class TestRetrievedContextFormatting(unittest.TestCase):
    def setUp(self):
        self.schema = _sales_schema()

    def test_to_prompt_text_includes_all_sections(self):
        result = retrieve("Which region has the highest revenue?", self.schema)
        text = result.to_prompt_text()
        self.assertIn("revenue", text.lower())
        self.assertIn("region", text.lower())

    def test_empty_context_produces_empty_prompt_text(self):
        from app.rag.retriever import RetrievedContext

        self.assertEqual(RetrievedContext().to_prompt_text(), "")


if __name__ == "__main__":
    unittest.main()
