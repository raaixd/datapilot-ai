"""
DataPilot AI -- Modern SaaS Business Analytics Interface.

A high-performance, dark-themed analytics console inspired by modern technical AI interfaces.
Provides natural-language SQL generation, automated profiling, multi-table schema discovery,
self-correcting query execution, and exportable PDF/Markdown reports.

Supports both standalone in-process execution and unified HTTP communication with the FastAPI backend.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import time

import pandas as pd
import streamlit as st

from app.agents.orchestrator import AnalysisResult, Orchestrator
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.data.database import AnalyticalDatabase
from app.data.loader import load_tabular_archive, load_tabular_file
from app.data.profiler import DataProfile, DataProfiler
from app.llm.factory import build_llm_client
from app.reports.markdown_report import render_markdown_report
from app.reports.pdf_report import render_pdf_report
from app.visualization.charts import build_chart
from frontend.api_client import VeridexApiClient

configure_logging()
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="VERIDEX",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# Modern Dark SaaS Design System (Hermes-Inspired Minimal Technical Theme)
# -----------------------------------------------------------------------------
st.markdown(
    """
    <style>
    :root {
        --dp-bg: #090B0E;
        --dp-surface: #11141B;
        --dp-surface-raised: #181C26;
        --dp-border: rgba(255, 255, 255, 0.08);
        --dp-border-active: rgba(56, 189, 248, 0.4);
        --dp-text-main: #F1F5F9;
        --dp-text-muted: #94A3B8;
        --dp-accent: #38BDF8;
        --dp-accent-glow: rgba(56, 189, 248, 0.15);
        --dp-success: #10B981;
        --dp-warning: #F59E0B;
        --dp-error: #EF4444;
        --dp-radius-sm: 6px;
        --dp-radius-md: 10px;
        --dp-radius-lg: 14px;
        --dp-font: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
    }

    /* Base canvas adjustments */
    .stApp {
        background-color: var(--dp-bg);
        color: var(--dp-text-main);
        font-family: var(--dp-font);
    }
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 3rem;
        max-width: 1280px;
    }

    /* Clean Application Header */
    .dp-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.8rem 1.4rem;
        background: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-radius: var(--dp-radius-lg);
        margin-bottom: 1.2rem;
    }
    .dp-brand {
        display: flex;
        align-items: center;
        gap: 0.75rem;
    }
    .dp-brand-logo {
        font-size: 1.3rem;
        color: var(--dp-accent);
        font-weight: 800;
        letter-spacing: -0.02em;
    }
    .dp-brand-title {
        font-size: 1.1rem;
        font-weight: 700;
        color: var(--dp-text-main);
        letter-spacing: -0.02em;
    }
    .dp-header-badges {
        display: flex;
        gap: 0.6rem;
        align-items: center;
    }
    .dp-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.2rem 0.65rem;
        background: var(--dp-surface-raised);
        border: 1px solid var(--dp-border);
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 500;
        color: var(--dp-text-muted);
    }
    .dp-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background-color: var(--dp-success);
    }

    /* Metric Cards */
    div[data-testid="stMetric"] {
        background-color: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-radius: var(--dp-radius-md);
        padding: 0.8rem 1rem;
    }
    div[data-testid="stMetric"] label {
        color: var(--dp-text-muted) !important;
        font-size: 0.8rem !important;
        font-weight: 500 !important;
    }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        color: var(--dp-text-main) !important;
        font-size: 1.4rem !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em;
    }

    /* Buttons */
    div[data-testid="stButton"] > button {
        border-radius: var(--dp-radius-sm);
        border: 1px solid var(--dp-border);
        background-color: var(--dp-surface-raised);
        color: var(--dp-text-main);
        font-size: 0.85rem;
        font-weight: 500;
        transition: all 0.15s ease;
    }
    div[data-testid="stButton"] > button:hover {
        border-color: var(--dp-accent);
        color: var(--dp-accent);
        box-shadow: 0 0 10px var(--dp-accent-glow);
    }
    div[data-testid="stButton"] > button[kind="primary"] {
        background-color: var(--dp-accent) !important;
        color: #090B0E !important;
        border: none !important;
        font-weight: 600 !important;
    }
    div[data-testid="stButton"] > button[kind="primary"]:hover {
        background-color: #7DD3FC !important;
        box-shadow: 0 0 14px rgba(56, 189, 248, 0.4) !important;
    }

    /* Clean Empty State Hero */
    .dp-empty-state {
        background: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-radius: var(--dp-radius-lg);
        padding: 2.5rem 2rem;
        text-align: center;
        margin: 1.5rem 0;
    }
    .dp-empty-headline {
        font-size: 1.5rem;
        font-weight: 700;
        color: var(--dp-text-main);
        margin-bottom: 0.5rem;
        letter-spacing: -0.02em;
    }
    .dp-empty-subtext {
        color: var(--dp-text-muted);
        font-size: 0.95rem;
        max-width: 600px;
        margin: 0 auto 2rem auto;
        line-height: 1.5;
    }
    .dp-workflow-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 1rem;
        max-width: 850px;
        margin: 0 auto 1.5rem auto;
        text-align: left;
    }
    .dp-step-card {
        background: var(--dp-surface-raised);
        border: 1px solid var(--dp-border);
        border-radius: var(--dp-radius-md);
        padding: 1.1rem;
    }
    .dp-step-number {
        font-size: 0.75rem;
        font-weight: 700;
        color: var(--dp-accent);
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.4rem;
    }
    .dp-step-title {
        font-size: 0.95rem;
        font-weight: 600;
        color: var(--dp-text-main);
        margin-bottom: 0.25rem;
    }
    .dp-step-desc {
        font-size: 0.8rem;
        color: var(--dp-text-muted);
        line-height: 1.4;
    }

    /* Severity chips */
    .dp-chip {
        display: inline-flex;
        align-items: center;
        padding: 0.15rem 0.55rem;
        border-radius: var(--dp-radius-sm);
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        margin-right: 0.5rem;
    }
    .dp-chip-critical { background: rgba(239, 68, 68, 0.15); color: #FCA5A5; border: 1px solid rgba(239, 68, 68, 0.3); }
    .dp-chip-warning  { background: rgba(245, 158, 11, 0.15); color: #FCD34D; border: 1px solid rgba(245, 158, 11, 0.3); }
    .dp-chip-info     { background: rgba(56, 189, 248, 0.15); color: #7DD3FC; border: 1px solid rgba(56, 189, 248, 0.3); }

    /* Results layout */
    .dp-insight-box {
        background: var(--dp-surface-raised);
        border: 1px solid var(--dp-border);
        border-radius: var(--dp-radius-md);
        padding: 1.2rem 1.4rem;
        margin-bottom: 1.2rem;
        font-size: 1.02rem;
        line-height: 1.6;
        color: #E2E8F0;
    }

    /* Tabs styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.4rem;
        border-bottom: 1px solid var(--dp-border);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: var(--dp-radius-sm) var(--dp-radius-sm) 0 0;
        padding: 0.5rem 1rem;
        color: var(--dp-text-muted);
        font-size: 0.85rem;
        font-weight: 500;
    }
    .stTabs [aria-selected="true"] {
        color: var(--dp-accent) !important;
        border-bottom: 2px solid var(--dp-accent) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

EXAMPLE_QUESTIONS = [
    "What is the total revenue?",
    "What are the top 5 regions by revenue?",
    "Show me the monthly revenue trend",
    "Compare revenue between regions",
    "Which products experienced declining sales?",
    "Are there any anomalies in revenue?",
    "How much data is missing?",
]


@st.cache_resource
def _get_shared_resources():
    settings = get_settings()
    try:
        llm = build_llm_client(settings)
    except Exception as exc:
        st.error(
            f"Could not initialize LLM client for LLM_PROVIDER={settings.llm_provider}: {exc}\n\n"
            "Set LLM_PROVIDER=mock in .env to run completely offline without an API key."
        )
        st.stop()
    return llm, DataProfiler(), settings


def _get_session_backend():
    if "session_db" not in st.session_state:
        llm, _profiler, settings = _get_shared_resources()
        db = AnalyticalDatabase.create_session_database(backend=settings.database_backend)
        orchestrator = Orchestrator(db, llm, max_result_rows=settings.max_result_rows, settings=settings)
        st.session_state.session_db = db
        st.session_state.session_orchestrator = orchestrator
        logger.info("Initialized session database: %s", db.path)
    return st.session_state.session_db, st.session_state.session_orchestrator


# Initialize session state keys
for k, default in [
    ("history", []),
    ("profiles", {}),
    ("tables", {}),
    ("active_table", None),
    ("last_result", None),
    ("question_input", ""),
    ("api_mode", False),
    ("api_base_url", os.getenv("API_BASE_URL", "http://localhost:8000")),
    ("api_session_id", None),
]:
    if k not in st.session_state:
        st.session_state[k] = default

_llm, profiler, settings = _get_shared_resources()
db, orchestrator = _get_session_backend()

# -----------------------------------------------------------------------------
# Application Header
# -----------------------------------------------------------------------------
provider_label = settings.llm_provider
if settings.llm_provider == "groq":
    provider_label = f"Groq ({settings.llm_model})"
elif settings.llm_provider == "anthropic":
    provider_label = f"Anthropic ({settings.llm_model})"
elif settings.llm_provider == "mock":
    provider_label = "Mock Client (Deterministic)"

mode_label = "API Client" if st.session_state.api_mode else "In-Process"

st.markdown(
    f"""
    <div class="dp-header">
        <div class="dp-brand">
            <span class="dp-brand-logo">◈</span>
            <span class="dp-brand-title">VERIDEX</span>
        </div>
        <div class="dp-header-badges">
            <span class="dp-pill"><span class="dp-dot"></span> {mode_label}</span>
            <span class="dp-pill">Engine: {settings.database_backend.upper()}</span>
            <span class="dp-pill">LLM: {provider_label}</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Sidebar: Table Manager, Upload, and Architecture Controls
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("#### Data Ingestion")
    uploaded = st.file_uploader(
        "Upload dataset",
        type=["csv", "xlsx", "xls", "zip"],
        help="Upload single tabular files (.csv, .xlsx) or a .zip archive containing multiple datasets.",
    )

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        use_sample = st.button("Sample Data", use_container_width=True)
    with col_s2:
        clear_all = st.button("Clear Session", use_container_width=True)

    if uploaded is not None:
        raw_bytes = uploaded.getvalue()
        fname = uploaded.name
        try:
            if fname.lower().endswith(".zip"):
                loaded = load_tabular_archive(io.BytesIO(raw_bytes), fname)
            else:
                df = load_tabular_file(io.BytesIO(raw_bytes), fname)
                tname = fname.rsplit(".", 1)[0]
                tname = "".join(ch if ch.isalnum() else "_" for ch in tname).strip("_") or "dataset"
                loaded = {tname: df}

            db.drop_all_tables()
            st.session_state.tables.clear()
            st.session_state.profiles.clear()

            for tname, tdf in loaded.items():
                schema = db.load_dataframe(tdf, tname)
                prof = profiler.profile(tdf, dataset_name=tname)
                st.session_state.tables[tname] = schema
                st.session_state.profiles[tname] = prof

            st.session_state.active_table = next(iter(loaded.keys()))
            st.session_state.last_result = None
            st.success(f"Loaded {len(loaded)} table(s) from {fname}")
        except Exception as exc:
            st.error(f"Upload failed: {exc}")

    if use_sample:
        try:
            df = pd.read_csv("data/sample_sales.csv")
            db.drop_all_tables()
            st.session_state.tables.clear()
            st.session_state.profiles.clear()

            schema = db.load_dataframe(df, "sample_sales")
            prof = profiler.profile(df, dataset_name="sample_sales.csv")
            st.session_state.tables["sample_sales"] = schema
            st.session_state.profiles["sample_sales"] = prof
            st.session_state.active_table = "sample_sales"
            st.session_state.last_result = None
            st.success("Loaded sample_sales dataset (1000 rows)")
        except Exception as exc:
            st.error(f"Could not load sample dataset: {exc}")

    if clear_all:
        db.drop_all_tables()
        st.session_state.tables.clear()
        st.session_state.profiles.clear()
        st.session_state.active_table = None
        st.session_state.last_result = None
        st.session_state.history = []
        st.session_state.question_input = ""
        st.rerun()

    # Loaded Tables Selector
    if st.session_state.tables:
        st.divider()
        st.markdown("#### Schema Registry")
        table_list = list(st.session_state.tables.keys())
        active = st.selectbox(
            "Active table inspection",
            table_list,
            index=table_list.index(st.session_state.active_table) if st.session_state.active_table in table_list else 0,
        )
        st.session_state.active_table = active

        relationships = db.detect_relationships()
        if relationships:
            st.caption(f"\u21c4 {len(relationships)} candidate relation(s) detected across tables.")

    # Architecture Options
    with st.expander("Architecture Settings", expanded=False):
        st.session_state.api_mode = st.toggle("Connect to FastAPI Backend", value=st.session_state.api_mode)
        if st.session_state.api_mode:
            st.session_state.api_base_url = st.text_input(
                "API Base URL",
                value=st.session_state.api_base_url,
            )
            api_client = VeridexApiClient(base_url=st.session_state.api_base_url)
            try:
                h = api_client.health()
                st.caption(f"Backend connected ({h.get('status')})")
            except Exception as e:
                st.caption(f"Backend unreachable: {e}")

    # Query History
    if st.session_state.history:
        st.divider()
        st.markdown("#### Recent Queries")
        for q in reversed(st.session_state.history[-6:]):
            st.caption(f"\u2022 {q}")

# -----------------------------------------------------------------------------
# Main Content Area
# -----------------------------------------------------------------------------
has_data = bool(st.session_state.tables)

if not has_data:
    # -------------------------------------------------------------------------
    # Clean Technical Empty State
    # -------------------------------------------------------------------------
    st.markdown(
        """
        <div class="dp-empty-state">
            <div class="dp-empty-headline">Deterministic SQL Analytics Over Tabular Data</div>
            <div class="dp-empty-subtext">
                Ingest single CSV/Excel files or multi-table ZIP archives. Query directly in plain English
                with code-enforced AST safety validation and self-correcting query execution.
            </div>
            <div class="dp-workflow-grid">
                <div class="dp-step-card">
                    <div class="dp-step-number">Step 01</div>
                    <div class="dp-step-title">Ingest Tabular Data</div>
                    <div class="dp-step-desc">Upload CSV, Excel (.xlsx), or ZIP archives containing multi-table schemas.</div>
                </div>
                <div class="dp-step-card">
                    <div class="dp-step-number">Step 02</div>
                    <div class="dp-step-title">Schema & Profiling</div>
                    <div class="dp-step-desc">Automated type inference, cardinality inspection, and quality warning detection.</div>
                </div>
                <div class="dp-step-card">
                    <div class="dp-step-number">Step 03</div>
                    <div class="dp-step-title">AST Safe Execution</div>
                    <div class="dp-step-desc">Strict read-only queries with self-correcting retry loops and traceable metric reporting.</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col_e1, col_e2, col_e3 = st.columns([1, 1, 1])
    with col_e2:
        if st.button("Load Sample Sales Dataset", type="primary", use_container_width=True):
            try:
                df = pd.read_csv("data/sample_sales.csv")
                db.drop_all_tables()
                schema = db.load_dataframe(df, "sample_sales")
                prof = profiler.profile(df, dataset_name="sample_sales.csv")
                st.session_state.tables["sample_sales"] = schema
                st.session_state.profiles["sample_sales"] = prof
                st.session_state.active_table = "sample_sales"
                st.rerun()
            except Exception as exc:
                st.error(f"Could not load sample data: {exc}")

else:
    active_tbl_name = st.session_state.active_table
    active_profile: DataProfile = st.session_state.profiles.get(active_tbl_name)
    active_schema = st.session_state.tables.get(active_tbl_name)

    # -------------------------------------------------------------------------
    # Dataset Overview & Schema Inspection
    # -------------------------------------------------------------------------
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Table", active_tbl_name)
    col_m2.metric("Rows", f"{active_profile.row_count:,}")
    col_m3.metric("Columns", active_profile.column_count)
    col_m4.metric("Duplicate Rows", active_profile.duplicate_row_count)

    with st.expander("Inspect Dataset Schema & Quality", expanded=False):
        tab_preview, tab_cols, tab_rels, tab_warn = st.tabs(["Preview", "Columns", "Relationships", "Quality Warnings"])

        with tab_preview:
            try:
                prev_df = db.query(f'SELECT * FROM "{active_tbl_name}" LIMIT 25', max_rows=25)
                st.dataframe(prev_df, use_container_width=True, height=220)
            except Exception as exc:
                st.caption(f"Preview unavailable: {exc}")

        with tab_cols:
            col_data = []
            for c in active_profile.columns:
                col_data.append(
                    {
                        "Column": c.name,
                        "SQL Type": next((col[1] for col in active_schema.columns if col[0] == c.name), c.dtype),
                        "Inferred Type": c.inferred_type,
                        "Null Count": c.null_count,
                        "Null %": f"{c.null_pct:.1f}%",
                        "Distinct": c.distinct_count,
                        "Sample Values": ", ".join(str(v) for v in c.sample_values[:3]),
                    }
                )
            st.dataframe(pd.DataFrame(col_data), use_container_width=True, height=220)

        with tab_rels:
            rels = db.detect_relationships()
            if rels:
                st.dataframe(pd.DataFrame(rels), use_container_width=True)
            else:
                st.caption("No cross-table foreign key or shared column relationships detected.")

        with tab_warn:
            if active_profile.warnings:
                for w in active_profile.warnings:
                    badge_cls = f"dp-chip dp-chip-{w.severity}"
                    st.markdown(
                        f'<span class="{badge_cls}">{w.severity}</span> **{w.column}**: {w.message}',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No data quality warnings flagged.")

    st.markdown("<div style='height: 0.8rem;'></div>", unsafe_allow_html=True)

    # -------------------------------------------------------------------------
    # Analysis Workspace
    # -------------------------------------------------------------------------
    st.markdown("#### Query Analysis")

    def _set_question(q_str: str):
        st.session_state["question_input"] = q_str

    # Curated Starter Chips
    st.caption("Quick Queries:")
    chip_cols = st.columns(len(EXAMPLE_QUESTIONS[:4]))
    for idx, ex_q in enumerate(EXAMPLE_QUESTIONS[:4]):
        with chip_cols[idx]:
            st.button(ex_q, key=f"chip_{idx}", on_click=_set_question, args=(ex_q,), use_container_width=True)

    with st.form(key="analysis_form", clear_on_submit=False):
        col_q, col_btn = st.columns([5, 1])
        with col_q:
            st.text_input(
                "Ask an analytical question",
                key="question_input",
                placeholder="e.g. What is the total revenue by product category?",
                label_visibility="collapsed",
            )
        with col_btn:
            submit_query = st.form_submit_button("Analyze", type="primary", use_container_width=True)

    current_q = st.session_state.get("question_input", "").strip()

    if submit_query and not current_q:
        st.warning("Enter an analytical question to run.")

    if submit_query and current_q:
        t0 = time.time()
        with st.spinner("Generating and verifying query..."):
            if st.session_state.api_mode:
                try:
                    client = VeridexApiClient(base_url=st.session_state.api_base_url)
                    # Use existing session or create on upload
                    sess_id = st.session_state.api_session_id or "default"
                    api_resp = client.query(current_q, session_id=sess_id)
                    res = AnalysisResult(
                        question=api_resp.get("question", current_q),
                        success=api_resp.get("success", False),
                        sql=api_resp.get("sql"),
                        insight=api_resp.get("insight"),
                        metrics=api_resp.get("metrics", {}),
                        result_preview=api_resp.get("result_preview", []),
                        chart_type=api_resp.get("chart_type"),
                        error=api_resp.get("error"),
                        llm_provider=api_resp.get("llm_provider"),
                        scope=api_resp.get("scope", "in_scope"),
                        clarification_options=api_resp.get("clarification_options", []),
                        notes=api_resp.get("notes", []),
                        retry_count=api_resp.get("retry_count", 0),
                        correction_history=api_resp.get("correction_history", []),
                    )
                except Exception as exc:
                    res = AnalysisResult(
                        question=current_q,
                        success=False,
                        error=f"API request failed: {exc}",
                        scope="in_scope",
                    )
            else:
                res = orchestrator.analyze(current_q, data_profile=active_profile)

        res.debug_info = f"Execution elapsed: {time.time() - t0:.3f}s"
        st.session_state.history.append(current_q)
        st.session_state.last_result = res

    # -------------------------------------------------------------------------
    # Results Presentation
    # -------------------------------------------------------------------------
    result: AnalysisResult | None = st.session_state.last_result
    if result is not None:
        if not result.success:
            if result.scope == "out_of_scope":
                st.info(result.error)
            elif result.scope == "ambiguous":
                st.warning(result.error)
                if result.clarification_options:
                    st.caption("Suggested options:")
                    cols = st.columns(min(len(result.clarification_options), 4))
                    for i, opt in enumerate(result.clarification_options):
                        with cols[i % len(cols)]:
                            opt_q = f"What is the total {opt}?"
                            st.button(opt, key=f"clarify_{opt}", on_click=_set_question, args=(opt_q,))
            elif result.scope == "unsafe":
                st.error(result.error)
            else:
                st.error(f"Could not execute query: {result.error}")
        else:
            # Executive Summary Insight
            st.markdown(
                f"""
                <div class="dp-insight-box">
                    <strong>Insight:</strong> {result.insight}
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Key Metrics Cards
            if result.metrics:
                numeric_metrics = {k: v for k, v in result.metrics.items() if isinstance(v, (int, float, str))}
                if numeric_metrics:
                    m_cols = st.columns(min(len(numeric_metrics), 4))
                    for idx, (m_key, m_val) in enumerate(list(numeric_metrics.items())[:4]):
                        val_str = f"{m_val:,.2f}" if isinstance(m_val, float) else str(m_val)
                        with m_cols[idx % 4]:
                            st.metric(m_key.replace("_", " ").title(), val_str)

            # Result Visuals & Export Tabs
            tab_chart, tab_data, tab_tech, tab_export = st.tabs(["Chart", "Data Table", "Technical / SQL", "Export"])

            with tab_chart:
                if result.sql is None:
                    st.caption("Answered from profile metadata; no tabular query to chart.")
                else:
                    fig = build_chart(result, dark_mode=True)
                    if fig is not None:
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.caption("No chart representation available for this result structure.")

            with tab_data:
                res_df = pd.DataFrame(result.result_preview)
                if not res_df.empty:
                    st.dataframe(res_df, use_container_width=True)
                    st.download_button(
                        "Download CSV",
                        res_df.to_csv(index=False),
                        file_name="veridex_results.csv",
                        mime="text/csv",
                    )
                else:
                    st.caption("Result set is empty.")

            with tab_tech:
                st.markdown("##### Validated SQL Query")
                if result.sql:
                    st.code(result.sql, language="sql")
                else:
                    st.caption("No SQL generated (metadata intent).")

                col_t1, col_t2 = st.columns(2)
                with col_t1:
                    st.caption("**Safety Status:** Validated Read-Only SELECT")
                    st.caption(f"**LLM Provider:** {result.llm_provider}")
                with col_t2:
                    st.caption(f"**Self-Correction Retries:** {result.retry_count}")
                    if result.debug_info:
                        st.caption(f"**Diagnostics:** {result.debug_info}")

                if result.correction_history:
                    st.caption("**Correction History:**")
                    st.json(result.correction_history)

            with tab_export:
                report_md = render_markdown_report(result)
                col_e1, col_e2 = st.columns(2)
                with col_e1:
                    st.download_button("Download Markdown Report", report_md, file_name="veridex_report.md")
                with col_e2:
                    try:
                        fd, pdf_path = tempfile.mkstemp(suffix=".pdf", prefix="veridex_report_")
                        os.close(fd)
                        try:
                            render_pdf_report(result, pdf_path)
                            with open(pdf_path, "rb") as f:
                                st.download_button(
                                    "Download PDF Report",
                                    f.read(),
                                    file_name="veridex_report.pdf",
                                    mime="application/pdf",
                                )
                        finally:
                            if os.path.exists(pdf_path):
                                os.remove(pdf_path)
                    except Exception as exc:
                        st.caption(f"PDF export unavailable: {exc}")

                st.markdown("---")
                st.markdown(report_md)

            # Follow-up Suggestions
            if result.follow_up_questions:
                st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)
                st.caption("Suggested Follow-ups:")
                f_cols = st.columns(min(len(result.follow_up_questions), 3))
                for idx, f_q in enumerate(result.follow_up_questions[:3]):
                    with f_cols[idx % 3]:
                        st.button(f_q, key=f"follow_{idx}", on_click=_set_question, args=(f_q,))
