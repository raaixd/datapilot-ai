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
            raise ValueError("ANTHROPIC_API_KEY is not set but LLM_PROVIDER=anthropic.")
        return AnthropicLLMClient(api_key=settings.anthropic_api_key, model=settings.llm_model)
    if settings.llm_provider == "groq":
        from app.llm.groq_client import GroqLLMClient

        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is not set but LLM_PROVIDER=groq.")
        model = (
            settings.llm_model
            if settings.llm_model and "claude" not in settings.llm_model
            else "llama-3.3-70b-versatile"
        )
        return GroqLLMClient(api_key=settings.groq_api_key, model=model)
    if settings.llm_provider == "ollama":
        from app.llm.anthropic_client import OllamaLLMClient

        return OllamaLLMClient(base_url=settings.ollama_base_url, model=settings.llm_model)
    raise ValueError(f"Unknown LLM_PROVIDER '{settings.llm_provider}'. Use 'mock', 'groq', 'anthropic', or 'ollama'.")
