"""
LLM client factory.

Two entry points:
  - build_llm_client(settings): builds a single provider client.
    Used by the test suite, the evaluation harness, and the CLI demo.
  - build_llm_client_with_fallback(settings): builds a FallbackLLMClient
    wrapping the primary provider (settings.primary_llm_provider) and the
    fallback (settings.fallback_llm_provider). Used by the FastAPI backend.
    Falls back transparently on any primary exception.

Supported providers: mock, gemini, groq, anthropic, ollama.
"""

from __future__ import annotations

import logging

from app.core.config import Settings
from app.llm.base import LLMClient
from app.llm.mock_client import MockLLMClient

logger = logging.getLogger(__name__)


def _build_single_provider(provider: str, settings: Settings) -> LLMClient:
    """Build a single LLM provider client by name."""
    if provider == "mock":
        return MockLLMClient()

    if provider == "gemini":
        from app.llm.gemini_client import GeminiLLMClient

        if not settings.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set but PRIMARY_LLM_PROVIDER=gemini (or LLM_PROVIDER=gemini). "
                "Set GEMINI_API_KEY in your .env file."
            )
        return GeminiLLMClient(api_key=settings.gemini_api_key, model=settings.gemini_model)

    if provider == "anthropic":
        from app.llm.anthropic_client import AnthropicLLMClient

        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set but provider=anthropic.")
        return AnthropicLLMClient(api_key=settings.anthropic_api_key, model=settings.llm_model)

    if provider == "groq":
        from app.llm.groq_client import GroqLLMClient

        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is not set but provider=groq.")
        model = (
            settings.llm_model
            if settings.llm_model and "claude" not in settings.llm_model
            else "llama-3.3-70b-versatile"
        )
        return GroqLLMClient(api_key=settings.groq_api_key, model=model)

    if provider == "ollama":
        from app.llm.anthropic_client import OllamaLLMClient

        return OllamaLLMClient(base_url=settings.ollama_base_url, model=settings.llm_model)

    raise ValueError(
        f"Unknown LLM provider '{provider}'. "
        "Valid options: 'mock', 'gemini', 'groq', 'anthropic', 'ollama'."
    )


def build_llm_client(settings: Settings) -> LLMClient:
    """Build a single provider client using settings.llm_provider.

    Preserved for backward compatibility with tests, eval harness, and CLI.
    The FastAPI backend should use build_llm_client_with_fallback() instead.
    """
    return _build_single_provider(settings.llm_provider, settings)


def build_llm_client_with_fallback(settings: Settings) -> LLMClient:
    """Build a FallbackLLMClient for production use.

    Uses settings.primary_llm_provider as the primary and
    settings.fallback_llm_provider as the secondary. If both are the same
    provider (or the fallback is 'mock'), returns a single client with no
    wrapping to avoid unnecessary overhead.

    Example configuration:
        PRIMARY_LLM_PROVIDER=gemini
        FALLBACK_LLM_PROVIDER=groq
    """
    primary_name = settings.primary_llm_provider
    fallback_name = settings.fallback_llm_provider

    primary = _build_single_provider(primary_name, settings)

    # No fallback needed if: same provider, mock primary (tests), or no fallback configured.
    if not fallback_name or fallback_name == primary_name or primary_name == "mock":
        logger.info("LLM: single provider mode (provider=%s)", primary_name)
        return primary

    try:
        fallback = _build_single_provider(fallback_name, settings)
    except Exception as exc:
        logger.warning(
            "Could not initialise fallback LLM provider '%s': %s. Running with primary only.",
            fallback_name,
            exc,
        )
        return primary

    from app.llm.fallback_client import FallbackLLMClient

    logger.info("LLM: primary=%s fallback=%s", primary_name, fallback_name)
    return FallbackLLMClient(primary=primary, fallback=fallback)

