"""
Tests for GeminiLLMClient and FallbackLLMClient.

These tests do NOT require a real Gemini API key or network access.
They use mocking to exercise the client logic in isolation.

Tests for the fallback chain are deterministic and run without any
LLM provider being installed.
"""

from __future__ import annotations

import pytest

from app.llm.fallback_client import FallbackLLMClient
from app.llm.mock_client import MockLLMClient

# ---------------------------------------------------------------------------
# FallbackLLMClient tests
# ---------------------------------------------------------------------------


class AlwaysSucceedsClient:
    """Stub LLM client that always returns a fixed string."""

    provider_name = "always_succeeds"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "success from primary"


class AlwaysFailsClient:
    """Stub LLM client that always raises an exception."""

    provider_name = "always_fails"

    def __init__(self, exc_type=RuntimeError, message="simulated failure"):
        self._exc_type = exc_type
        self._message = message

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise self._exc_type(self._message)


class SuccessOnSecondCallClient:
    """Stub that fails once then succeeds — not used in FallbackLLMClient
    directly but useful for verifying fallback semantics."""

    provider_name = "succeeds_on_second_call"
    _call_count = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self._call_count += 1
        if self._call_count == 1:
            raise RuntimeError("first call fails")
        return "success on second call"


class TestFallbackLLMClient:
    """FallbackLLMClient delegates to primary when it succeeds."""

    def test_primary_success_returns_primary_result(self):
        primary = AlwaysSucceedsClient()
        fallback = AlwaysFailsClient()
        client = FallbackLLMClient(primary=primary, fallback=fallback)
        result = client.complete("system", "user")
        assert result == "success from primary"

    def test_provider_name_reflects_primary_on_success(self):
        primary = AlwaysSucceedsClient()
        fallback = AlwaysFailsClient()
        client = FallbackLLMClient(primary=primary, fallback=fallback)
        client.complete("system", "user")
        assert client.provider_name == "always_succeeds"

    def test_primary_failure_falls_back_to_secondary(self):
        primary = AlwaysFailsClient()
        fallback = AlwaysSucceedsClient()
        client = FallbackLLMClient(primary=primary, fallback=fallback)
        # Replace fallback to return a distinct string
        class DistinctFallback:
            provider_name = "distinct_fallback"
            def complete(self, s, u):
                return "success from fallback"
        client._fallback = DistinctFallback()
        result = client.complete("system", "user")
        assert result == "success from fallback"

    def test_provider_name_reflects_fallback_when_primary_fails(self):
        primary = AlwaysFailsClient()
        fallback = AlwaysSucceedsClient()
        client = FallbackLLMClient(primary=primary, fallback=fallback)
        client.complete("system", "user")
        assert "fallback" in client.provider_name.lower() or client.provider_name == "always_succeeds"

    def test_both_fail_propagates_fallback_exception(self):
        primary = AlwaysFailsClient(RuntimeError, "primary failed")
        fallback = AlwaysFailsClient(ValueError, "fallback also failed")
        client = FallbackLLMClient(primary=primary, fallback=fallback)
        with pytest.raises(ValueError, match="fallback also failed"):
            client.complete("system", "user")

    def test_different_exception_types_all_trigger_fallback(self):
        """Any exception type from the primary should trigger fallback."""
        for exc_type in (RuntimeError, ConnectionError, TimeoutError, ValueError, OSError):
            primary = AlwaysFailsClient(exc_type, f"{exc_type.__name__} from primary")
            fallback = AlwaysSucceedsClient()
            client = FallbackLLMClient(primary=primary, fallback=fallback)
            # Should not raise — fallback succeeds
            result = client.complete("system", "user")
            assert "success" in result

    def test_initial_provider_name_is_primary_name(self):
        primary = AlwaysSucceedsClient()
        fallback = AlwaysFailsClient()
        client = FallbackLLMClient(primary=primary, fallback=fallback)
        assert client.provider_name == "always_succeeds"


class TestFallbackWithMockClient:
    """FallbackLLMClient works with the real MockLLMClient as either provider."""

    def test_mock_as_primary_succeeds(self):
        mock = MockLLMClient()
        fallback = AlwaysFailsClient()
        client = FallbackLLMClient(primary=mock, fallback=fallback)
        # MockLLMClient requires a loaded schema to answer non-trivial questions;
        # a simple TASK: insight prompt should return something non-empty.
        result = client.complete(
            "You are a helpful assistant.",
            "TASK: insight\nQUESTION: test\nSQL: SELECT 1\nRESULT_PREVIEW:\n(no rows)\nMETRICS:\n{}",
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_mock_as_fallback_when_primary_fails(self):
        primary = AlwaysFailsClient()
        fallback = MockLLMClient()
        client = FallbackLLMClient(primary=primary, fallback=fallback)
        result = client.complete(
            "You are a helpful assistant.",
            "TASK: insight\nQUESTION: test\nSQL: SELECT 1\nRESULT_PREVIEW:\n(no rows)\nMETRICS:\n{}",
        )
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# GeminiLLMClient import and construction tests (no network required)
# ---------------------------------------------------------------------------


class TestGeminiClientConstruction:
    """Verify GeminiLLMClient raises clearly when misconfigured."""

    def test_empty_api_key_raises_value_error(self):
        from app.llm.gemini_client import GeminiLLMClient

        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GeminiLLMClient(api_key="")

    def test_whitespace_only_key_raises_value_error(self):
        from app.llm.gemini_client import GeminiLLMClient

        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GeminiLLMClient(api_key="   ")

    def test_valid_key_constructs_without_error(self):
        from app.llm.gemini_client import GeminiLLMClient

        # Should not raise (does NOT call the API during construction)
        client = GeminiLLMClient(api_key="AIza_fake_key_for_unit_test", model="gemini-2.0-flash")
        assert client is not None

    def test_complete_raises_import_error_without_sdk(self, monkeypatch):
        """When google-generativeai is not installed, complete() raises ImportError
        with a clear installation instruction (not a raw import stack trace)."""
        import builtins

        from app.llm.gemini_client import GeminiLLMClient
        real_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "google.generativeai":
                raise ImportError("No module named 'google.generativeai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _mock_import)
        client = GeminiLLMClient(api_key="AIza_fake_for_test")
        # Force client reset so _get_client() re-runs import
        client._client = None
        with pytest.raises(ImportError, match="google-generativeai"):
            client.complete("system", "user")


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestFactory:
    """build_llm_client still works; build_llm_client_with_fallback works."""

    def test_build_llm_client_mock(self):
        from app.core.config import Settings
        from app.llm.factory import build_llm_client
        from app.llm.mock_client import MockLLMClient

        settings = Settings()
        client = build_llm_client(settings)
        assert isinstance(client, MockLLMClient)

    def test_build_llm_client_unknown_provider_raises(self):

        from app.core.config import Settings

        # Temporarily override LLM_PROVIDER via environment isn't possible with frozen
        # dataclass; test the factory directly.
        from app.llm.factory import _build_single_provider
        settings = Settings()
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            _build_single_provider("nonexistent_provider", settings)

    def test_build_with_fallback_mock_primary_returns_single_client(self):
        """When primary is 'mock', no fallback wrapper is needed."""
        from app.core.config import Settings
        from app.llm.factory import build_llm_client_with_fallback
        from app.llm.mock_client import MockLLMClient

        settings = Settings()  # llm_provider defaults to 'mock'
        client = build_llm_client_with_fallback(settings)
        # Should be a plain MockLLMClient, not wrapped in FallbackLLMClient
        assert isinstance(client, MockLLMClient)

    def test_build_with_fallback_same_primary_and_fallback_returns_single(self):
        """When primary == fallback, no wrapping needed."""
        # Simulate: PRIMARY_LLM_PROVIDER=mock, FALLBACK_LLM_PROVIDER=mock
        import dataclasses

        from app.core.config import Settings
        from app.llm.factory import build_llm_client_with_fallback
        from app.llm.mock_client import MockLLMClient
        settings = dataclasses.replace(
            Settings(),
            primary_llm_provider="mock",
            fallback_llm_provider="mock",
        )
        client = build_llm_client_with_fallback(settings)
        assert isinstance(client, MockLLMClient)
