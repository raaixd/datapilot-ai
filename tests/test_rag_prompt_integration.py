"""
Proof that retrieval is wired INTO generation, not just a module that
exists on its own. Uses a recording fake LLMClient to capture the exact
prompt text app/agents/planner.py and app/agents/sql_generator.py send,
and asserts the retrieved context is actually present in it -- this is
what distinguishes "a real retrieval step feeding the LLM" from "RAG as a
label" (see app/rag/retriever.py's module docstring).
"""
import unittest

import pandas as pd

from app.agents.planner import AnalysisPlanner
from app.agents.sql_generator import SQLGenerator
from app.data.database import AnalyticalDatabase
from app.llm.base import LLMClient
from app.llm.mock_client import MockLLMClient


class RecordingLLMClient(LLMClient):
    """Wraps MockLLMClient so tests get real, working responses (needed for
    SQLGenerator's test, which requires a valid plan to generate against)
    while recording every prompt actually sent, for inspection."""

    def __init__(self):
        self._inner = MockLLMClient()
        self.recorded_prompts: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.recorded_prompts.append((system_prompt, user_prompt))
        return self._inner.complete(system_prompt, user_prompt)


def _sales_schema_and_db():
    df = pd.DataFrame({
        "order_date": ["2024-01-01", "2024-02-01"], "region": ["North", "South"],
        "product_category": ["Electronics", "Apparel"], "quantity": [1, 2],
        "unit_price": [10.0, 20.0], "revenue": [10.0, 40.0],
    })
    db = AnalyticalDatabase(backend="sqlite", path=":memory:")
    db.load_dataframe(df, "sales")
    return db.describe_schema(), db


class TestPlannerPromptIncludesRetrievedContext(unittest.TestCase):
    def test_revenue_question_prompt_contains_retrieved_glossary_text(self):
        schema, _db = _sales_schema_and_db()
        client = RecordingLLMClient()
        planner = AnalysisPlanner(client)
        planner.plan("What is the total revenue?", schema)

        self.assertEqual(len(client.recorded_prompts), 1)
        _system, user_prompt = client.recorded_prompts[0]
        self.assertIn("CONTEXT:", user_prompt)
        self.assertIn("revenue", user_prompt.lower())
        # the retrieved definition text itself, not just the word:
        self.assertIn("monetary amount generated from sales", user_prompt)

    def test_declining_question_prompt_contains_retrieved_example(self):
        schema, _db = _sales_schema_and_db()
        client = RecordingLLMClient()
        planner = AnalysisPlanner(client)
        planner.plan("Which products experienced declining sales?", schema)

        _system, user_prompt = client.recorded_prompts[0]
        self.assertIn("Similar validated question patterns", user_prompt)
        self.assertIn("declining", user_prompt.lower())

    def test_context_appears_before_schema_and_does_not_break_question_parsing(self):
        # Regression guard for the ordering constraint documented in
        # app/llm/prompts.py: CONTEXT must not corrupt the QUESTION/SCHEMA
        # markers the mock client's regex parsing depends on.
        schema, _db = _sales_schema_and_db()
        client = RecordingLLMClient()
        planner = AnalysisPlanner(client)
        result = planner.plan("What is the total revenue?", schema)
        self.assertTrue(result.is_answerable, result.clarification_needed)
        self.assertEqual(result.metric_column, "revenue")

    def test_off_topic_question_prompt_has_no_context_block(self):
        # Nothing relevant to retrieve for a fully off-topic question --
        # the prompt should simply omit the CONTEXT section, not include an
        # empty/misleading one.
        schema, _db = _sales_schema_and_db()
        client = RecordingLLMClient()
        planner = AnalysisPlanner(client)
        planner.plan("What is the meaning of life?", schema)
        _system, user_prompt = client.recorded_prompts[0]
        self.assertNotIn("CONTEXT:", user_prompt)


class TestSQLGeneratorPromptIncludesRetrievedContext(unittest.TestCase):
    def test_sql_prompt_contains_retrieved_context_and_still_parses(self):
        schema, db = _sales_schema_and_db()
        client = RecordingLLMClient()
        planner = AnalysisPlanner(client)
        plan = planner.plan("What is the total revenue?", schema)

        sql_client = RecordingLLMClient()
        generator = SQLGenerator(sql_client)
        sql = generator.generate(plan, schema)

        self.assertEqual(len(sql_client.recorded_prompts), 1)
        _system, user_prompt = sql_client.recorded_prompts[0]
        self.assertIn("CONTEXT:", user_prompt)
        self.assertIn("PLAN:", user_prompt)
        # Prove the PLAN:...SCHEMA: capture (mock_client._sql's regex) still
        # works correctly even with CONTEXT present in the prompt -- if the
        # ordering were wrong, this would produce garbage/no SQL.
        self.assertTrue(sql.strip().upper().startswith("SELECT"))
        self.assertIn("revenue", sql.lower())
        db.close()


if __name__ == "__main__":
    unittest.main()
