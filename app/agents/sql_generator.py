from __future__ import annotations

import json
from dataclasses import asdict

from app.agents.planner import AnalysisPlan, describe_schema_text
from app.data.database import TableSchema
from app.llm.base import LLMClient
from app.llm.prompts import SQL_GENERATOR_SYSTEM_PROMPT, build_sql_user_prompt
from app.rag.retriever import retrieve


class SQLGenerator:
    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    def generate(self, plan: AnalysisPlan, schema: dict[str, TableSchema]) -> str:
        schema_text = describe_schema_text(schema)
        retrieved = retrieve(plan.question, schema)
        user_prompt = build_sql_user_prompt(json.dumps(asdict(plan)), schema_text, retrieved.to_prompt_text())
        raw_sql = self._llm.complete(SQL_GENERATOR_SYSTEM_PROMPT, user_prompt)
        return _strip_code_fence_and_prose(raw_sql)


def _strip_code_fence_and_prose(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:] if lines and lines[0].startswith("```") else lines
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
