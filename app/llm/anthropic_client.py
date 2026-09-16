"""
Anthropic-backed LLM client.

NOTE ON TESTING: this file was written to the same `LLMClient` interface as
`MockLLMClient` and is syntax-checked, but it has NOT been executed against
the live Anthropic API in this environment (no network access / no API key
available here). Exercise it locally with a real ANTHROPIC_API_KEY before
relying on it. The application, tests, and evaluation harness all default
to MockLLMClient (LLM_PROVIDER=mock) specifically so they don't need this
file to work.
"""
from __future__ import annotations

from app.llm.base import LLMClient


class AnthropicLLMClient(LLMClient):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for LLM_PROVIDER=anthropic. "
                "Install it with `pip install anthropic`."
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")


class OllamaLLMClient(LLMClient):
    """Local-model client, for running the same pipeline with zero API key
    via an Ollama server (see OLLAMA_BASE_URL in app/core/config.py)."""

    def __init__(self, base_url: str, model: str = "llama3.1"):
        self._base_url = base_url.rstrip("/")
        self._model = model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        import json
        import urllib.request

        payload = json.dumps({
            "model": self._model,
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{self._base_url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["response"]
