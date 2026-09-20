"""
Fallback LLM Client.

Wraps a primary and a fallback LLMClient. On any exception from the primary,
logs the failure (without exposing API keys or stack traces to the user) and
transparently delegates to the fallback provider.

Design principles:
- The rest of the application is completely unaware this wrapper exists.
  It only sees a single LLMClient interface.
- provider_name reports which provider actually answered, not which was
  requested. This is surfaced in AnalysisResult.llm_provider and in API
  responses so users always know whether Gemini or Groq answered.
- Fallback is triggered on ANY exception from the primary — network errors,
  rate limits, content policy rejections. This makes the system resilient
  to transient Gemini outages without manual intervention.
- MAX_FALLBACK_ATTEMPTS = 1: we try primary, then fallback. If the fallback
  also fails, the exception propagates and the orchestrator returns a
  controlled error to the user. We do not retry indefinitely.
"""

from __future__ import annotations

import logging

from app.llm.base import LLMClient

logger = logging.getLogger(__name__)


class FallbackLLMClient(LLMClient):
    """LLM client that automatically falls back to a secondary provider
    when the primary raises any exception.

    Usage:
        primary = GeminiLLMClient(api_key=...)
        fallback = GroqLLMClient(api_key=...)
        client = FallbackLLMClient(primary=primary, fallback=fallback)

    The client exposes `last_provider_used` so the orchestrator can record
    which provider actually answered for a given analysis run.
    """

    def __init__(self, primary: LLMClient, fallback: LLMClient):
        self._primary = primary
        self._fallback = fallback
        self._last_provider: str = getattr(primary, "provider_name", "primary")

    @property
    def provider_name(self) -> str:  # type: ignore[override]
        """The name of the provider that answered the most recent request.

        Returns the primary's name initially. Reflects the actual provider
        used after each call to complete().
        """
        return self._last_provider

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Attempt completion with the primary; fall back to secondary on failure.

        Structured log events are emitted for:
        - llm_fallback_triggered: primary failed, falling back
        - llm_fallback_also_failed: both providers failed

        These events are consumed by CloudWatch metrics in production and
        by structured log review in local mode.
        """
        primary_name = getattr(self._primary, "provider_name", "primary")
        fallback_name = getattr(self._fallback, "provider_name", "fallback")

        try:
            result = self._primary.complete(system_prompt, user_prompt)
            self._last_provider = primary_name
            return result
        except Exception as primary_exc:
            logger.warning(
                '{"event": "llm_fallback_triggered", "primary": "%s", "fallback": "%s",'
                ' "reason": "%s"}',
                primary_name,
                fallback_name,
                type(primary_exc).__name__,
            )

        try:
            result = self._fallback.complete(system_prompt, user_prompt)
            self._last_provider = f"{fallback_name}(fallback)"
            logger.info(
                '{"event": "llm_fallback_succeeded", "provider": "%s"}',
                fallback_name,
            )
            return result
        except Exception as fallback_exc:
            logger.error(
                '{"event": "llm_fallback_also_failed", "primary": "%s", "fallback": "%s",'
                ' "fallback_reason": "%s"}',
                primary_name,
                fallback_name,
                type(fallback_exc).__name__,
            )
            # Re-raise the fallback exception — the orchestrator will return a
            # controlled "AI analysis temporarily unavailable" message.
            raise
