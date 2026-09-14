"""
Application configuration.

Deliberately implemented with the standard library `dataclasses` module
instead of pydantic. The analysis core (profiler, validator, analytics,
orchestrator) does not depend on pydantic at all -- pydantic is only used
at the FastAPI edge (app/api/schemas.py) to validate HTTP request/response
bodies. This keeps the core testable and importable in any environment,
even one where the web dependencies are not installed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    """Runtime configuration, populated from environment variables."""

    # LLM provider settings. "mock" requires no key and is used by the
    # test suite and the evaluation harness so neither needs network access.
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "mock"))
    freellmapi_api_key: str | None = field(
        default_factory=lambda: os.getenv("FREELLMAPI_API_KEY")
    )
    freellmapi_base_url: str = field(
        default_factory=lambda: os.getenv(
            "FREELLMAPI_BASE_URL",
            "http://localhost:3001/v1",
        )
    )
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-4-6"))

    # Data layer
    database_backend: str = field(default_factory=lambda: os.getenv("DATABASE_BACKEND", "sqlite"))  # "sqlite" or "duckdb"
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "data/datapilot.db"))

    # SQL safety
    max_result_rows: int = field(default_factory=lambda: int(os.getenv("MAX_RESULT_ROWS", "1000")))
    query_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("QUERY_TIMEOUT_SECONDS", "10")))

    # Reports
    reports_dir: str = field(default_factory=lambda: os.getenv("REPORTS_DIR", "outputs/reports"))


def get_settings() -> Settings:
    return Settings()
