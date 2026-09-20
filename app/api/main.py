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
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import analyses, datasets, projects, reports, system
from app.api.schemas import DataQualityWarningOut, QueryRequest, QueryResponse, UploadResponse
from app.api.session_manager import SessionManager, SessionNotFoundError
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.core.observability import get_metrics_collector, setup_observability
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
    title="VERIDEX",
    version="2.0.0",
    description="Cloud-native natural-language business analytics over tabular datasets. "
    "Supports single CSV/Excel and multi-table ZIP archives with session isolation.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_settings = get_settings()
setup_observability(_settings)


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start_time = time.perf_counter()
    collector = get_metrics_collector()
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        response.headers["X-Request-ID"] = request_id
        collector.record_request(
            path=request.url.path,
            method=request.method,
            status_code=response.status_code,
            latency_ms=duration_ms,
        )
        logger.info(
            "Request %s %s %d (%.2f ms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        collector.record_request(
            path=request.url.path,
            method=request.method,
            status_code=500,
            latency_ms=duration_ms,
        )
        logger.exception(
            "Unhandled server error during request %s %s: %s",
            request.method,
            request.url.path,
            exc,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round(duration_ms, 2),
                "error": str(exc),
            },
        )
        raise

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

# Connect system session counter
system.set_active_sessions_getter(lambda: _sessions.active_session_count())

# Mount REST API routers
app.include_router(projects.router)
app.include_router(datasets.router)
app.include_router(analyses.router)
app.include_router(reports.router)
app.include_router(system.router)

# Mount REST API routers under /api/v1
app.include_router(projects.router, prefix="/api/v1")
app.include_router(datasets.router, prefix="/api/v1")
app.include_router(analyses.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")


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

