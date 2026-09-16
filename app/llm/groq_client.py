"""
Groq LLM Client.

Uses the Groq API (OpenAI-compatible endpoints at https://api.groq.com/openai/v1)
to provide ultra-fast inference for Llama 3 models without local GPUs.

Adheres strictly to the LLMClient interface so the rest of the application
(planner, sql_generator, orchestrator) remains completely provider-agnostic.
"""

from __future__ import annotations

import logging
from typing import Any

from app.llm.base import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqLLMClient(LLMClient):
    """LLM client for Groq's high-speed inference cloud."""

    provider_name = "groq"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GROQ_MODEL,
        base_url: str = GROQ_BASE_URL,
        timeout: float = 30.0,
    ):
        if not api_key or not api_key.strip():
            raise ValueError(
                "GROQ_API_KEY is not set or empty, but LLM_PROVIDER=groq. "
                "Set GROQ_API_KEY in your .env or environment before running."
            )

        self._api_key = api_key.strip()
        self._model = model or DEFAULT_GROQ_MODEL
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Any = None

        # Mask key for safe logging
        masked = self._api_key[:4] + "..." + self._api_key[-4:] if len(self._api_key) > 8 else "***"
        logger.info("Initialized GroqLLMClient (model=%s, key=%s)", self._model, masked)

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=self._api_key,
                    base_url=self._base_url,
                    timeout=self._timeout,
                )
            except ImportError as exc:
                raise ImportError(
                    "The 'openai' package is required for GroqLLMClient. Install it with `pip install openai`."
                ) from exc
        return self._client

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=1024,
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("Groq API returned an empty completion.")
            return content.strip()
        except Exception as exc:
            # Never include raw authorization headers or keys in logs
            logger.error("Groq API request failed for model %s: %s", self._model, exc)
            raise
