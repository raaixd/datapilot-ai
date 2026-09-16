"""
LLM client interface.

Every concrete client (mock, Anthropic, Ollama, ...) implements this one
method. The rest of the application only ever talks to `LLMClient`, so the
provider can be swapped by changing `LLM_PROVIDER` in the environment
(see app/llm/factory.py) without touching agents/orchestrator code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMClient(ABC):
    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the raw text completion for a single-turn prompt."""
        raise NotImplementedError
