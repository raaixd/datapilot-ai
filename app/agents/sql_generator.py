from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

from app.agents.planner import AnalysisPlan, describe_schema_text
from app.data.database import TableSchema
from app.llm.base import LLMClient
from app.llm.prompts import (
    SQL_CORRECTION_SYSTEM_PROMPT,
    SQL_GENERATOR_SYSTEM_PROMPT,
    build_sql_correction_prompt,
    build_sql_user_prompt,
)
from app.rag.retriever import retrieve


class SQLGenerator:
    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    def generate(
        self,
        plan: AnalysisPlan,
        schema: dict[str, TableSchema],
        semantic_schema: Any = None,
    ) -> str:
        schema_text = describe_schema_text(schema, semantic_schema=semantic_schema)
        retrieved = retrieve(plan.question, schema)
        user_prompt = build_sql_user_prompt(json.dumps(asdict(plan)), schema_text, retrieved.to_prompt_text())
        raw_sql = self._llm.complete(SQL_GENERATOR_SYSTEM_PROMPT, user_prompt)
        return _strip_code_fence_and_prose(raw_sql)

    def correct(
        self,
        failing_sql: str,
        error_message: str,
        schema: dict[str, TableSchema],
        question: str = "",
        semantic_schema: Any = None,
        error_type: str | None = None,
    ) -> str:
        schema_text = describe_schema_text(schema, semantic_schema=semantic_schema)
        user_prompt = build_sql_correction_prompt(
            failing_sql=failing_sql,
            error_message=error_message,
            schema_description=schema_text,
            question=question,
            error_type=error_type,
        )
        raw_sql = self._llm.complete(SQL_CORRECTION_SYSTEM_PROMPT, user_prompt)
        return _strip_code_fence_and_prose(raw_sql)


def _strip_code_fence_and_prose(text: str) -> str:
    # Strip any chain-of-thought blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:] if lines and lines[0].startswith("```") else lines
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
