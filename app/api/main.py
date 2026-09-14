"""
FastAPI application.

NOTE ON TESTING: this file requires `fastapi`, `uvicorn`, and `pydantic`,
none of which are installed in the sandbox this project was developed in
(no network access to pip install them here). It was written carefully
against the FastAPI API and syntax-checked with `python -m py_compile`,
but it has NOT actually been started or hit with a real HTTP request in
this environment. `app/agents/orchestrator.py`, `app/data/loader.py`, and
everything else this endpoint calls INTO have been executed and unit
tested (see tests/). Before relying on this file, run it locally:

    pip install -r requirements.txt
    uvicorn app.api.main:app --reload
    curl http://localhost:8000/health

and confirm it behaves as documented in README.md's "Running it" section.

Startup behavior: the LLM client and database are constructed once at
import time (module-level `_llm`, `_db`), so a bad LLM_PROVIDER value or a
missing ANTHROPIC_API_KEY fails loudly and immediately when the server
starts, rather than on the first request.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.api.schemas import DataQualityWarningOut, HealthResponse, QueryRequest, QueryResponse
from app.api.state import DatasetRegistry
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.data.loader import MissingOptionalDependencyError, UnsupportedFileTypeError, load_tabular_file

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="DataPilot AI",
    version="0.2.0",
    description="Natural-language business analytics over an uploaded CSV/Excel file. "
                 "See /health for current configuration.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_settings = get_settings()

try:
    from app.llm.factory import build_llm_client
    _llm = build_llm_client(_settings)
except Exception:
    logger.exception(
        "Failed to construct the LLM client for LLM_PROVIDER=%s. Check your .env "
        "(see .env.example) before retrying.", _settings.llm_provider,
    )
    raise

_dataset_registry = DatasetRegistry(_settings, llm_factory=lambda _settings: _llm)


@app.post("/upload")
async def upload_dataset(file: Annotated[UploadFile, File(...)], dataset: str = "default"):
    """Accepts a .csv, .xlsx, or .xls file, profiles it, and loads it as the
    active table (replacing any previously loaded dataset -- this API holds
    exactly one active dataset at a time; see README 'How the application
    resets between datasets')."""
    raw = await file.read()
    import io

    try:
        df = load_tabular_file(io.BytesIO(raw), file.filename or "upload.csv")
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MissingOptionalDependencyError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse '{file.filename}': {exc}") from exc

    table_name = (file.filename or "dataset").rsplit(".", 1)[0]
    state = _dataset_registry.load_dataframe(dataset, df, table_name)
    schema = state.schema
    logger.info("Loaded dataset '%s' (%d rows) as table '%s'", file.filename, schema.row_count, schema.name)
    return {
        "table": schema.name,
        "row_count": schema.row_count,
        "columns": [c[0] for c in schema.columns],
        "profile": state.profile.to_dict(),
    }


@app.post("/query", response_model=QueryResponse)
async def run_query(request: QueryRequest):
    state = _dataset_registry.get(request.dataset)
    if state is None:
        raise HTTPException(status_code=400, detail=f"No dataset named '{request.dataset}' is loaded. POST a file to /upload first.")

    result = state.orchestrator.analyze(request.question, data_profile=state.profile)
    return QueryResponse(
        question=result.question,
        success=result.success,
        sql=result.sql,
        insight=result.insight,
        metrics=result.metrics,
        result_preview=result.result_preview,
        chart_type=result.chart_type,
        plan=(result.plan.__dict__ if result.plan else None),
        follow_up_questions=result.follow_up_questions,
        data_quality_warnings=[DataQualityWarningOut(**vars(w)) for w in result.data_quality_warnings],
        error=result.error,
        llm_provider=result.llm_provider,
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    """Confirms the API is up and reports its actual configuration (LLM
    provider, database backend, whether a dataset is currently loaded) --
    check this first if the frontend can't reach the backend."""
    return HealthResponse(
        status="healthy",
        llm_provider=_settings.llm_provider,
        database_backend=_settings.database_backend,
        tables_loaded=_dataset_registry.count(),
    )
