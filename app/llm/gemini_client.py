"""
Google Gemini LLM Client.

Uses the `google-generativeai` SDK to call Gemini Flash/Pro models.
This is the primary LLM provider in the cloud-native architecture.
Falls back to Groq (via FallbackLLMClient) on failure.

API key is read from GEMINI_API_KEY.
Model is read from GEMINI_MODEL (default: gemini-2.0-flash).

This client adheres strictly to the LLMClient interface — the rest of the
application (planner, sql_generator, orchestrator) remains completely
provider-agnostic. The provider name is recorded in AnalysisResult.llm_provider
so users always know whether a real model answered.
"""

from __future__ import annotations

import logging
from typing import Any

from app.llm.base import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"


class GeminiLLMClient(LLMClient):
    """LLM client for Google Gemini via the google-generativeai SDK."""

    provider_name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        timeout: float = 60.0,
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
    ):
        if not api_key or not api_key.strip():
            raise ValueError(
                "GEMINI_API_KEY is not set or empty. "
                "Set GEMINI_API_KEY in your .env before running with PRIMARY_LLM_PROVIDER=gemini."
            )

        self._api_key = api_key.strip()
        self._model = model or DEFAULT_GEMINI_MODEL
        self._timeout = timeout
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._client: Any = None

        masked = self._api_key[:4] + "..." + self._api_key[-4:] if len(self._api_key) > 8 else "***"
        logger.info("Initialized GeminiLLMClient (model=%s, key=%s)", self._model, masked)

    def _get_client(self) -> Any:
        """Lazy-initialise the Gemini client to avoid import-time failures
        when the package is not installed (tests use MockLLMClient instead)."""
        if self._client is None:
            try:
                import google.generativeai as genai  # type: ignore[import]
            except ImportError as exc:
                raise ImportError(
                    "The 'google-generativeai' package is required for GeminiLLMClient. "
                    "Install it with `pip install google-generativeai`."
                ) from exc

            genai.configure(api_key=self._api_key)
            self._client = genai.GenerativeModel(
                model_name=self._model,
                generation_config={
                    "temperature": self._temperature,
                    "max_output_tokens": self._max_output_tokens,
                    "candidate_count": 1,
                },
            )
        return self._client

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Call Gemini with a combined system+user prompt.

        Gemini's GenerativeModel API does not have a separate 'system' role
        in the same way as OpenAI's chat API. The system prompt is prepended
        to the user turn with a clear delimiter, which is how Gemini models
        are expected to receive instructions.
        """
        client = self._get_client()
        # Combine system and user prompts into a single structured message.
        # Gemini supports system_instruction separately only in certain SDK versions;
        # using a combined prompt is the most compatible approach.
        combined_prompt = f"{system_prompt.strip()}\n\n---\n\n{user_prompt.strip()}"
        try:
            response = client.generate_content(combined_prompt)
            text = response.text
            if not text or not text.strip():
                raise RuntimeError("Gemini API returned an empty completion.")
            logger.debug("Gemini response: %d chars from model=%s", len(text), self._model)
            return text.strip()
        except Exception as exc:
            # Never log the raw API key or response headers.
            logger.error("Gemini API request failed (model=%s): %s", self._model, type(exc).__name__)
            raise
