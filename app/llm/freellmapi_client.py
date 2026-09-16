from __future__ import annotations

from openai import OpenAI

from app.llm.base import LLMClient


class FreeLLMAPIClient(LLMClient):
    """LLM client for the local FreeLLMAPI OpenAI-compatible server."""

    provider_name = "freellmapi"

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:3001/v1",
        model: str = "auto",
    ):
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self._model = model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=4096,
        )

        content = response.choices[0].message.content

        if not content:
            raise RuntimeError("FreeLLMAPI returned an empty response.")

        return content
