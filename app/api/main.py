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
from app.data.loader import MissingOptionalDependencyError, UnsupportedFileTypeError, load_tabular_file
from app.data.profiler import DataProfiler

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="DataPilot AI",
    version="0.3.0",
    description="Natural-language business analytics over an uploaded CSV/Excel file. "
                 "Each client gets an isolated session -- see /upload. Check /health for current configuration.",
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

_sessions = SessionManager(_llm, _settings, ttl_seconds=_settings.session_ttl_minutes * 60)
_profiler = DataProfiler()


@app.post("/upload", response_model=UploadResponse)
async def upload_dataset(file: UploadFile = File(...), session_id: str | None = Form(default=None)):
    """Upload a .csv, .xlsx, or .xls file. If `session_id` is omitted, a new
    isolated session is created and returned -- pass it back on subsequent
    /query calls. If `session_id` is provided and still active, the new
    file REPLACES that session's current table (see README 'How the
    application resets between datasets')."""
    raw = await file.read()

    size_mb = len(raw) / (1024 * 1024)
    if size_mb > _settings.max_upload_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File is {size_mb:.1f} MB, which exceeds the {_settings.max_upload_mb} MB limit "
                    f"(set via MAX_UPLOAD_MB).",
        )

    try:
        df = load_tabular_file(io.BytesIO(raw), file.filename or "upload.csv")
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MissingOptionalDependencyError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse '{file.filename}': {exc}") from exc

    if session_id:
        try:
            state = _sessions.get_session(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        state = _sessions.create_session()

    state.profile = _profiler.profile(df, dataset_name=file.filename or "uploaded.csv")
    table_name = (file.filename or "dataset").rsplit(".", 1)[0]
    schema = state.db.load_dataframe(df, table_name)
    logger.info("Session %s: loaded '%s' (%d rows) as table '%s'", state.session_id, file.filename, schema.row_count, schema.name)

    return UploadResponse(
        session_id=state.session_id,
        table=schema.name,
        row_count=schema.row_count,
        columns=[c[0] for c in schema.columns],
        profile=state.profile.to_dict(),
    )


@app.post("/query", response_model=QueryResponse)
async def run_query(request: QueryRequest):
    try:
        state = _sessions.get_session(request.session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not state.db.list_tables():
        raise HTTPException(status_code=400, detail="No dataset loaded in this session yet. POST a file to /upload first.")

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
