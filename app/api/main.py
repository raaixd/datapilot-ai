"""
FastAPI application.

NOTE ON TESTING: this file requires `fastapi`, `uvicorn`, and `pydantic`,
none of which are installed in the sandbox this project was developed in
(no network access to pip install them here). It was written carefully
against the FastAPI API and syntax-checked with `python -m py_compile`,
but it has NOT actually been started or hit with a real HTTP request in
this environment. All the actual session-isolation LOGIC this file calls
into (app/api/session_manager.py) HAS been executed and unit tested (see
tests/test_session_manager.py, 12 tests) -- this file is deliberately just
a thin HTTP wrapper around it for exactly that reason: the more logic that
lives in an untestable-here layer, the more risk. Before relying on this
file, run it locally:

    pip install -r requirements.txt
    uvicorn app.api.main:app --reload
    curl http://localhost:8000/health

SESSION MODEL (round 3 fix): each client gets an isolated session via
POST /upload (which returns a `session_id`), and must pass that
`session_id` on every subsequent POST /query. This replaced a single
global dataset shared by every client -- see
app/api/session_manager.py's module docstring for the full writeup of
what was wrong and why this fixes it.
"""

from __future__ import annotations

import io
import logging

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.api.schemas import DataQualityWarningOut, HealthResponse, QueryRequest, QueryResponse, UploadResponse
from app.api.session_manager import SessionManager, SessionNotFoundError
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.data.loader import (
    MissingOptionalDependencyError,
    UnsupportedFileTypeError,
    load_tabular_archive,
    load_tabular_file,
)
from app.data.profiler import DataProfiler

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="DataPilot AI",
    version="0.4.0",
    description="Natural-language business analytics over uploaded tabular datasets. "
    "Supports single CSV/Excel and multi-table ZIP archives with session isolation.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_settings = get_settings()

try:
    from app.llm.factory import build_llm_client

    _llm = build_llm_client(_settings)
except Exception:
    logger.exception(
        "Failed to construct the LLM client for LLM_PROVIDER=%s. Check your .env (see .env.example) before retrying.",
        _settings.llm_provider,
    )
    raise

_sessions = SessionManager(_llm, _settings, ttl_seconds=_settings.session_ttl_minutes * 60)
_profiler = DataProfiler()


@app.post("/upload", response_model=UploadResponse)
async def upload_dataset(file: UploadFile = File(...), session_id: str | None = Form(default=None)):  # noqa: B008
    """Upload a .csv, .xlsx, .xls, or .zip archive containing tables.

    If `session_id` is omitted, a new isolated session is created and returned.
    """
    raw = await file.read()
    filename = file.filename or "upload.csv"

    size_mb = len(raw) / (1024 * 1024)
    if size_mb > _settings.max_upload_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File is {size_mb:.1f} MB, which exceeds the {_settings.max_upload_mb} MB limit "
            f"(set via MAX_UPLOAD_MB).",
        )

    if session_id:
        try:
            state = _sessions.get_session(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        state = _sessions.create_session()

    tables_dict = {}
    if filename.lower().endswith(".zip"):
        try:
            tables_dict = load_tabular_archive(io.BytesIO(raw), filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        try:
            df = load_tabular_file(io.BytesIO(raw), filename)
            table_name = filename.rsplit(".", 1)[0]
            table_name = "".join(ch if ch.isalnum() else "_" for ch in table_name).strip("_") or "dataset"
            tables_dict[table_name] = df
        except UnsupportedFileTypeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except MissingOptionalDependencyError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Could not parse '{filename}': {exc}") from exc

    state.db.drop_all_tables()
    loaded_schemas = {}
    for tbl_name, df in tables_dict.items():
        loaded_schemas[tbl_name] = state.db.load_dataframe(df, tbl_name)

    # Profile primary table
    primary_tbl = next(iter(tables_dict))
    state.profile = _profiler.profile(tables_dict[primary_tbl], dataset_name=primary_tbl)

    schema_all = state.db.describe_schema()
    relationships = state.db.detect_relationships(schema_all)

    logger.info(
        "Session %s: loaded %d table(s) from '%s'",
        state.session_id,
        len(tables_dict),
        filename,
    )

    primary_schema = loaded_schemas[primary_tbl]
    return UploadResponse(
        session_id=state.session_id,
        table=primary_schema.name,
        row_count=primary_schema.row_count,
        columns=[c[0] for c in primary_schema.columns],
        profile=state.profile.to_dict(),
        tables=list(schema_all.keys()),
        relationships=relationships,
    )


@app.get("/tables/{session_id}")
async def get_session_tables(session_id: str):
    """List loaded tables, schema metadata, and detected relationships for a session."""
    try:
        state = _sessions.get_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    schema = state.db.describe_schema()
    relationships = state.db.detect_relationships(schema)
    return {
        "session_id": session_id,
        "tables": [
            {
                "name": t.name,
                "row_count": t.row_count,
                "columns": [{"name": c[0], "type": c[1]} for c in t.columns],
            }
            for t in schema.values()
        ],
        "relationships": relationships,
    }


@app.post("/query", response_model=QueryResponse)
async def run_query(request: QueryRequest):
    try:
        state = _sessions.get_session(request.session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not state.db.list_tables():
        raise HTTPException(
            status_code=400, detail="No dataset loaded in this session yet. POST a file to /upload first."
        )

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
        scope=result.scope,
        clarification_options=result.clarification_options,
        notes=result.notes,
        retry_count=result.retry_count,
        correction_history=result.correction_history,
    )


@app.delete("/session/{session_id}")
async def end_session(session_id: str):
    """Explicitly end a session and free its temp database file. Sessions
    also expire automatically after SESSION_TTL_MINUTES of inactivity
    (default 120) -- this endpoint is for clients that want to clean up
    immediately rather than wait for expiry."""
    deleted = _sessions.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' was not found.")
    return {"status": "deleted", "session_id": session_id}


@app.get("/health", response_model=HealthResponse)
async def health():
    """Confirms the API is up and reports its actual configuration (LLM
    provider, database backend, active session count) -- check this first
    if a client can't get a sensible response from /query."""
    return HealthResponse(
        status="healthy",
        llm_provider=_settings.llm_provider,
        database_backend=_settings.database_backend,
        active_sessions=_sessions.active_session_count(),
    )
