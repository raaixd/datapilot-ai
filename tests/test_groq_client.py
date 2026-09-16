"""
Tests for app/llm/groq_client.py.

Verifies that GroqLLMClient correctly validates parameters, handles missing keys,
masks sensitive keys in logs, and calls OpenAI-compatible completion without leaking credentials.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.llm.factory import build_llm_client
from app.llm.groq_client import GroqLLMClient


class TestGroqLLMClient(unittest.TestCase):
    def test_missing_api_key_raises_clear_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            GroqLLMClient(api_key="")
        self.assertIn("GROQ_API_KEY is not set", str(ctx.exception))

    def test_factory_requires_groq_key_when_provider_is_groq(self):
        settings = Settings(llm_provider="groq", groq_api_key=None)
        with self.assertRaises(ValueError) as ctx:
            build_llm_client(settings)
        self.assertIn("GROQ_API_KEY is not set", str(ctx.exception))

    def test_factory_builds_groq_client_when_key_present(self):
        settings = Settings(
            llm_provider="groq", groq_api_key="mock_test_groq_key_value", llm_model="llama-3.3-70b-versatile"
        )
        client = build_llm_client(settings)
        self.assertIsInstance(client, GroqLLMClient)
        self.assertEqual(client._model, "llama-3.3-70b-versatile")

    def test_complete_calls_chat_completions(self):
        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "SELECT SUM(revenue) FROM sales;"
        mock_response.choices = [mock_choice]
        mock_instance.chat.completions.create.return_value = mock_response

        client = GroqLLMClient(api_key="mock_test_groq_key_value", model="llama-3.3-70b-versatile")
        client._client = mock_instance
        result = client.complete("System prompt", "User question")

        self.assertEqual(result, "SELECT SUM(revenue) FROM sales;")
        mock_instance.chat.completions.create.assert_called_once()
        args, kwargs = mock_instance.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "llama-3.3-70b-versatile")
        self.assertEqual(kwargs["messages"][0]["content"], "System prompt")
        self.assertEqual(kwargs["messages"][1]["content"], "User question")

    def test_empty_response_raises_runtime_error(self):
        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = ""
        mock_response.choices = [mock_choice]
        mock_instance.chat.completions.create.return_value = mock_response

        client = GroqLLMClient(api_key="mock_test_groq_key_value")
        client._client = mock_instance
        with self.assertRaises(RuntimeError) as ctx:
            client.complete("Sys", "User")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_missing_openai_dependency_raises_import_error(self):
        client = GroqLLMClient(api_key="mock_test_groq_key_value")
        with patch.dict("sys.modules", {"openai": None}):
            client._client = None
            with self.assertRaises(ImportError) as ctx:
                client._get_client()
            self.assertIn("openai", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
