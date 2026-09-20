"""
Application configuration.

Deliberately implemented with the standard library `dataclasses` module
instead of pydantic. The analysis core (profiler, validator, analytics,
orchestrator) does not depend on pydantic at all -- pydantic is only used
at the FastAPI edge (app/api/schemas.py) to validate HTTP request/response
bodies. This keeps the core testable and importable in any environment,
even one where the web dependencies are not installed.

Cloud-native additions (Phase 1+):
  - Gemini as primary LLM provider with Groq fallback
  - LOCAL_MODE for development without AWS costs
  - AWS configuration (region, S3 bucket)
  - DATABASE_URL for PostgreSQL/RDS (falls back to SQLite for local dev)
  - MAX_SQL_REPAIR_ATTEMPTS (was hardcoded to 2 in orchestrator)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, populated from environment variables."""

    # ------------------------------------------------------------------ #
    # LLM provider settings (original)                                    #
    # ------------------------------------------------------------------ #
    # "mock" requires no key and is used by the test suite and the
    # evaluation harness so neither needs network access.
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "mock"))
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    groq_api_key: str | None = field(default_factory=lambda: os.getenv("GROQ_API_KEY"))
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-4-6"))
    api_base_url: str | None = field(default_factory=lambda: os.getenv("API_BASE_URL"))

    # ------------------------------------------------------------------ #
    # Gemini (primary cloud LLM)                                          #
    # ------------------------------------------------------------------ #
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    )

    # ------------------------------------------------------------------ #
    # Primary / fallback provider chain                                   #
    # PRIMARY_LLM_PROVIDER=gemini / FALLBACK_LLM_PROVIDER=groq           #
    # If not set, falls back to LLM_PROVIDER for backwards compatibility. #
    # ------------------------------------------------------------------ #
    primary_llm_provider: str = field(
        default_factory=lambda: os.getenv("PRIMARY_LLM_PROVIDER") or os.getenv("LLM_PROVIDER", "mock")
    )
    fallback_llm_provider: str = field(
        default_factory=lambda: os.getenv("FALLBACK_LLM_PROVIDER", "")
    )

    # ------------------------------------------------------------------ #
    # Data layer (original)                                               #
    # ------------------------------------------------------------------ #
    database_backend: str = field(
        default_factory=lambda: os.getenv("DATABASE_BACKEND", "sqlite")
    )  # "sqlite" or "duckdb"
    database_path: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_PATH",
            "data/datapilot.db" if os.path.exists("data/datapilot.db") else "data/veridex.db",
        )
    )

    # ------------------------------------------------------------------ #
    # Application metadata database (PostgreSQL/RDS or local SQLite)      #
    # ------------------------------------------------------------------ #
    # For local development: sqlite:///data/veridex_meta.db (default)     #
    # For production: postgresql://user:pass@rds-host:5432/veridex        #
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "sqlite:///data/datapilot_meta.db"
            if os.path.exists("data/datapilot_meta.db")
            else "sqlite:///data/veridex_meta.db",
        )
    )

    # ------------------------------------------------------------------ #
    # AWS configuration                                                   #
    # ------------------------------------------------------------------ #
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    aws_s3_bucket: str = field(default_factory=lambda: os.getenv("AWS_S3_BUCKET", ""))
    # Explicit credentials — only used when not running inside AWS with an IAM role.
    # Never commit values. Prefer IAM roles in production.
    aws_access_key_id: str | None = field(default_factory=lambda: os.getenv("AWS_ACCESS_KEY_ID"))
    aws_secret_access_key: str | None = field(default_factory=lambda: os.getenv("AWS_SECRET_ACCESS_KEY"))

    # ------------------------------------------------------------------ #
    # Local development mode                                              #
    # ------------------------------------------------------------------ #
    # LOCAL_MODE=true: use local filesystem instead of S3, local DB       #
    # instead of RDS, direct pipeline calls instead of Lambda triggers.  #
    local_mode: bool = field(
        default_factory=lambda: os.getenv("LOCAL_MODE", "true").lower() in ("true", "1", "yes")
    )
    # Root directory for local S3 simulation (LOCAL_MODE only)
    local_storage_root: str = field(
        default_factory=lambda: os.getenv("LOCAL_STORAGE_ROOT", "data/local_s3")
    )

    # ------------------------------------------------------------------ #
    # SQL safety (original + new)                                         #
    # ------------------------------------------------------------------ #
    max_result_rows: int = field(default_factory=lambda: int(os.getenv("MAX_RESULT_ROWS", "1000")))
    query_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("QUERY_TIMEOUT_SECONDS", "10")))
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "50")))
    session_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("SESSION_TTL_MINUTES", "120")))
    # Was hardcoded to 2 in orchestrator.py. Now configurable.
    max_sql_repair_attempts: int = field(
        default_factory=lambda: int(os.getenv("MAX_SQL_REPAIR_ATTEMPTS", "2"))
    )

    # ------------------------------------------------------------------ #
    # Reports                                                             #
    # ------------------------------------------------------------------ #
    reports_dir: str = field(default_factory=lambda: os.getenv("REPORTS_DIR", "outputs/reports"))

    # ------------------------------------------------------------------ #
    # Observability                                                       #
    # ------------------------------------------------------------------ #
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    cloudwatch_enabled: bool = field(
        default_factory=lambda: os.getenv("CLOUDWATCH_ENABLED", "false").lower() in ("true", "1", "yes")
    )
    # CloudWatch log group for API service (used in production only)
    cloudwatch_log_group: str = field(
        default_factory=lambda: os.getenv("CLOUDWATCH_LOG_GROUP", "/veridex/api")
    )


def get_settings() -> Settings:
    return Settings()

