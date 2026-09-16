"""
Minimal, centralized logging setup.

Call `configure_logging()` once at process start (CLI demo, API startup,
Streamlit startup). Library modules (orchestrator, validator, etc.) just do
`logger = logging.getLogger(__name__)` and log normally -- they never call
`basicConfig` themselves, so importing them doesn't have the side effect of
reconfiguring logging for whatever process imports them.
"""
from __future__ import annotations

import logging
import os


def configure_logging(level: str | None = None) -> None:
    resolved_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, resolved_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
