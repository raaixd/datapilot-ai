from __future__ import annotations

from app.core.config import Settings
from app.llm.base import LLMClient
from app.llm.mock_client import MockLLMClient


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "mock":
        return MockLLMClient()

    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_client import AnthropicLLMClient

        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set but "
                "LLM_PROVIDER=anthropic."
            )

        return AnthropicLLMClient(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
        )

    if settings.llm_provider == "ollama":
        from app.llm.anthropic_client import OllamaLLMClient

        return OllamaLLMClient(
            base_url=settings.ollama_base_url,
            model=settings.llm_model,
        )

    if settings.llm_provider == "freellmapi":
        from app.llm.freellmapi_client import FreeLLMAPIClient

        if not settings.freellmapi_api_key:
            raise ValueError(
                "FREELLMAPI_API_KEY is not set but "
                "LLM_PROVIDER=freellmapi."
            )

        return FreeLLMAPIClient(
            api_key=settings.freellmapi_api_key,
            base_url=settings.freellmapi_base_url,
            model=settings.llm_model,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER '{settings.llm_provider}'. "
        "Use 'mock', 'anthropic', 'ollama', or 'freellmapi'."
    )
