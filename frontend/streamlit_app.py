"""
VERIDEX -- Cloud-Native AI Data Analyst Platform.

A high-performance, dark-themed analytics console inspired by modern technical AI interfaces.
Consumes the Stage 4 REST API with full support for:
  - Project Workspaces: project lifecycle, dataset association, and metadata tracking
  - Ingestion & Profiling: deterministic data profiling, quality assessment, and semantic schema discovery
  - AI Analyst: natural-language SQL analysis, AST validation, self-correcting query execution, and grounded insights
  - Verified SQL & Audit Trail: syntax validation, repair tracking, error categorization, and latency telemetry
  - Grounded Visualizations: data-compatible Plotly charts (line, bar, pie, scatter)
  - 10-Section Analytical Reports: deterministic publication-ready report compilation and markdown export
  - Operational Observability: real-time telemetry (p50/p90/p99 latencies, error rates, throughput)

Also provides seamless fallback to in-process execution when running offline without the FastAPI backend.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import pandas as pd
import streamlit as st

from app.agents.orchestrator import AnalysisResult, Orchestrator
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.data.database import AnalyticalDatabase
from app.data.loader import load_tabular_file
from app.data.profiler import DataProfiler
from app.llm.factory import build_llm_client
from app.reports.markdown_report import render_markdown_report
from app.visualization.charts import build_chart
from frontend.api_client import APIClientError, VeridexApiClient

configure_logging()
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="VERIDEX AI Data Analyst",
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
        padding-top: 1.0rem;
        padding-bottom: 3rem;
        max-width: 1320px;
    }

    /* Application Header */
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
        font-size: 1.4rem;
        color: var(--dp-accent);
        font-weight: 800;
        line-height: 1;
    }
    .dp-brand-title {
        font-size: 1.15rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: var(--dp-text-main);
    }
    .dp-brand-subtitle {
        font-size: 0.8rem;
        color: var(--dp-text-muted);
        margin-left: 0.2rem;
        font-weight: 400;
    }
    .dp-header-badges {
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .dp-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.25rem 0.65rem;
        background: var(--dp-surface-raised);
        border: 1px solid var(--dp-border);
        border-radius: 9999px;
        font-size: 0.75rem;
        color: var(--dp-text-muted);
        font-weight: 500;
    }
    .dp-dot-online {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background-color: var(--dp-success);
        box-shadow: 0 0 6px var(--dp-success);
    }
    .dp-dot-offline {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background-color: var(--dp-warning);
    }

    /* Cards & Panels */
    .dp-card {
        background: var(--dp-surface);
        border: 1px solid var(--dp-border);
        border-radius: var(--dp-radius-md);
        padding: 1.2rem;
        margin-bottom: 1.0rem;
    }
    .dp-card-title {
        font-size: 0.95rem;
        font-weight: 600;
        color: var(--dp-text-main);
        margin-bottom: 0.4rem;
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
    .dp-chip-success  { background: rgba(16, 185, 129, 0.15); color: #6EE7B7; border: 1px solid rgba(16, 185, 129, 0.3); }

    /* Insight callout box */
    .dp-insight-box {
        background: linear-gradient(135deg, rgba(56, 189, 248, 0.08) 0%, rgba(24, 28, 38, 0.8) 100%);
        border: 1px solid rgba(56, 189, 248, 0.3);
        border-radius: var(--dp-radius-md);
        padding: 1.2rem 1.4rem;
        margin-bottom: 1.2rem;
        font-size: 1.05rem;
        line-height: 1.6;
        color: #F8FAFC;
    }

    /* Tabs styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.4rem;
        border-bottom: 1px solid var(--dp-border);
        margin-bottom: 1.2rem;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: var(--dp-radius-sm) var(--dp-radius-sm) 0 0;
        padding: 0.6rem 1.1rem;
        color: var(--dp-text-muted);
        font-size: 0.88rem;
        font-weight: 500;
    }
    .stTabs [aria-selected="true"] {
        color: var(--dp-accent) !important;
        border-bottom: 2px solid var(--dp-accent) !important;
        background: rgba(56, 189, 248, 0.05);
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
            f"Could not initialize LLM client: {exc}\n\n"
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
    return st.session_state.session_db, st.session_state.session_orchestrator


# -----------------------------------------------------------------------------
# Initialize Session State
# -----------------------------------------------------------------------------
for k, default in [
    ("api_mode", True),
    ("api_base_url", os.getenv("API_BASE_URL", "http://localhost:8000")),
    ("active_project_id", None),
    ("active_dataset_id", None),
    ("active_dataset_name", None),
    ("project_cache", []),
    ("dataset_cache", {}),  # project_id -> list of dataset dicts
    ("profile_cache", {}),  # dataset_id -> profile dict
    ("analysis_history", []),  # list of executed analysis runs
    ("last_result", None),
    ("question_input", ""),
    # Fallback in-process state
    ("tables", {}),
    ("profiles", {}),
    ("active_table", None),
]:
    if k not in st.session_state:
        st.session_state[k] = default

_llm, profiler, settings = _get_shared_resources()
in_proc_db, in_proc_orchestrator = _get_session_backend()

# Instantiate API Client
api_client = VeridexApiClient(base_url=st.session_state.api_base_url)

# Probe Backend Connection
backend_online = False
backend_version = "Unknown"
if st.session_state.api_mode:
    try:
        h = api_client.health()
        backend_online = h.get("status") == "healthy"
        backend_version = h.get("version", "2.0.0")
    except Exception:
        backend_online = False

# -----------------------------------------------------------------------------
# Application Header
# -----------------------------------------------------------------------------
mode_badge = (
    f'<span class="dp-pill"><span class="dp-dot-online"></span> REST API v{backend_version}</span>'
    if backend_online
    else '<span class="dp-pill"><span class="dp-dot-offline"></span> In-Process Mode</span>'
)

st.markdown(
    f"""
    <div class="dp-header">
        <div class="dp-brand">
            <span class="dp-brand-logo">◈</span>
            <div>
                <span class="dp-brand-title">VERIDEX</span>
                <span class="dp-brand-subtitle">AI Data Analyst Platform</span>
            </div>
        </div>
        <div class="dp-header-badges">
            {mode_badge}
            <span class="dp-pill">Backend: {settings.database_backend.upper()}</span>
            <span class="dp-pill">LLM: {settings.llm_provider.upper()}</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Sidebar: Navigation & Context Switcher
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ◈ Workspace Context")

    # API Mode & Server Configuration
    with st.expander("API Server Settings", expanded=not backend_online):
        st.session_state.api_mode = st.toggle("Use REST API Backend", value=st.session_state.api_mode)
        st.session_state.api_base_url = st.text_input(
            "API Base URL",
            value=st.session_state.api_base_url,
        )
        if st.button("Check Connection", use_container_width=True):
            st.rerun()

    # Load Projects in API Mode
    projects_list: list[dict[str, Any]] = []
    if st.session_state.api_mode and backend_online:
        try:
            resp = api_client.list_projects()
            projects_list = resp.get("projects", [])
            st.session_state.project_cache = projects_list
        except Exception as exc:
            st.warning(f"Could not list projects: {exc}")

    if projects_list:
        proj_options = {p["id"]: p["name"] for p in projects_list}
        selected_proj_id = st.selectbox(
            "Active Project",
            options=list(proj_options.keys()),
            format_func=lambda pid: proj_options[pid],
            index=0 if st.session_state.active_project_id not in proj_options else list(proj_options.keys()).index(st.session_state.active_project_id),
        )
        st.session_state.active_project_id = selected_proj_id

        # Datasets under this project
        cur_datasets = st.session_state.dataset_cache.get(selected_proj_id, [])
        if cur_datasets:
            ds_options = {d["id"]: d["filename"] for d in cur_datasets}
            selected_ds_id = st.selectbox(
                "Active Dataset",
                options=list(ds_options.keys()),
                format_func=lambda did: ds_options[did],
                index=0 if st.session_state.active_dataset_id not in ds_options else list(ds_options.keys()).index(st.session_state.active_dataset_id),
            )
            st.session_state.active_dataset_id = selected_ds_id
            st.session_state.active_dataset_name = ds_options[selected_ds_id]
        else:
            st.caption("No datasets uploaded to this project yet.")
    else:
        st.caption("No active projects found. Create one in the Projects tab.")

    st.markdown("---")

    # Quick Sample Data Loader
    if st.button("Load Sample Sales Data", use_container_width=True):
        if st.session_state.api_mode and backend_online:
            try:
                # Ensure a project exists
                p_resp = api_client.create_project(
                    name=f"Sample Sales Demo {int(time.time())}",
                    description="Automated sample e-commerce dataset workspace",
                )
                pid = p_resp["id"]
                st.session_state.active_project_id = pid

                with open("data/sample_sales.csv", "rb") as f:
                    csv_bytes = f.read()

                ds_resp = api_client.upload_project_dataset(pid, csv_bytes, "sample_sales.csv")
                did = ds_resp["id"]
                st.session_state.active_dataset_id = did
                st.session_state.active_dataset_name = "sample_sales.csv"

                if pid not in st.session_state.dataset_cache:
                    st.session_state.dataset_cache[pid] = []
                st.session_state.dataset_cache[pid].append(ds_resp)

                st.success("Sample dataset loaded into REST API workspace!")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to load sample data via API: {exc}")
        else:
            # Fallback in-process
            try:
                df = pd.read_csv("data/sample_sales.csv")
                in_proc_db.drop_all_tables()
                schema = in_proc_db.load_dataframe(df, "sample_sales")
                prof = profiler.profile(df, dataset_name="sample_sales.csv")
                st.session_state.tables["sample_sales"] = schema
                st.session_state.profiles["sample_sales"] = prof
                st.session_state.active_table = "sample_sales"
                st.session_state.active_dataset_name = "sample_sales.csv"
                st.success("Sample dataset loaded in-process!")
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to load sample dataset: {exc}")

    # Clear Workspace Button
    if st.button("Reset Session", use_container_width=True):
        st.session_state.active_project_id = None
        st.session_state.active_dataset_id = None
        st.session_state.active_dataset_name = None
        st.session_state.last_result = None
        st.session_state.analysis_history = []
        st.session_state.question_input = ""
        st.session_state.tables.clear()
        st.session_state.profiles.clear()
        st.rerun()


# -----------------------------------------------------------------------------
# Main Product Workspace Tabs
# -----------------------------------------------------------------------------
tab_projects, tab_profile, tab_analyst, tab_reports, tab_observability = st.tabs([
    "📁 Projects & Datasets",
    "📊 Dataset Profile",
    "🤖 AI Analyst",
    "📑 Analytical Reports",
    "📈 Operational Observability",
])

# =============================================================================
# TAB 1: Projects & Datasets Workspace
# =============================================================================
with tab_projects:
    st.markdown("### 📁 Project Workspaces & Data Ingestion")
    st.caption("Organize business analytics into isolated project environments with deterministic data ingestion.")

    col_p1, col_p2 = st.columns([1, 1.4], gap="large")

    with col_p1:
        st.markdown("#### Create New Project")
        with st.form("create_project_form"):
            new_proj_name = st.text_input("Project Name", placeholder="e.g. Q4 Revenue Analysis")
            new_proj_desc = st.text_area("Description (Optional)", placeholder="Project goals, stakeholder context, etc.")
            create_proj_submit = st.form_submit_button("Create Project", type="primary", use_container_width=True)

        if create_proj_submit:
            if not new_proj_name.strip():
                st.warning("Please enter a project name.")
            elif st.session_state.api_mode and backend_online:
                try:
                    p = api_client.create_project(name=new_proj_name, description=new_proj_desc)
                    st.success(f"Project '{p['name']}' created successfully!")
                    st.session_state.active_project_id = p["id"]
                    st.rerun()
                except APIClientError as exc:
                    if exc.status_code == 409:
                        st.error(f"A project named '{new_proj_name}' already exists. Please choose a distinct name.")
                    else:
                        st.error(f"Failed to create project: {exc.detail}")
                except Exception as exc:
                    st.error(f"Error connecting to backend: {exc}")
            else:
                # In-process mock project
                st.session_state.active_project_id = f"local-proj-{int(time.time())}"
                st.success(f"Project '{new_proj_name}' created (In-Process mode).")

        st.markdown("---")
        st.markdown("#### Active Project Details")
        active_pid = st.session_state.active_project_id
        if active_pid and st.session_state.api_mode and backend_online:
            try:
                p_meta = api_client.get_project(active_pid)
                st.markdown(f"**Name:** {p_meta.get('name')}")
                st.markdown(f"**ID:** `{p_meta.get('id')}`")
                if p_meta.get("description"):
                    st.caption(f"_{p_meta.get('description')}_")
                st.caption(f"Created: {p_meta.get('created_at', 'N/A')}")
            except Exception:
                st.caption(f"Active Project ID: `{active_pid}`")
        elif active_pid:
            st.caption(f"Active Project ID: `{active_pid}`")
        else:
            st.info("No active project selected. Create or select a project above.")

    with col_p2:
        st.markdown("#### Upload Dataset")
        active_pid = st.session_state.active_project_id
        if not active_pid:
            st.info("Select or create a project before uploading datasets.")
        else:
            uploaded_file = st.file_uploader(
                "Select Tabular Dataset",
                type=["csv", "xlsx", "xls", "zip"],
                help="Upload CSV or Excel files. Multi-table ZIP archives will be profiled deterministically.",
            )

            if uploaded_file is not None:
                file_bytes = uploaded_file.getvalue()
                filename = uploaded_file.name

                if st.button("Ingest & Profile Dataset", type="primary", use_container_width=True):
                    with st.spinner("Uploading and running deterministic profiling pipeline..."):
                        if st.session_state.api_mode and backend_online:
                            try:
                                ds_resp = api_client.upload_project_dataset(active_pid, file_bytes, filename)
                                did = ds_resp["id"]
                                st.session_state.active_dataset_id = did
                                st.session_state.active_dataset_name = filename

                                if active_pid not in st.session_state.dataset_cache:
                                    st.session_state.dataset_cache[active_pid] = []
                                st.session_state.dataset_cache[active_pid].append(ds_resp)

                                st.success(f"Dataset '{filename}' successfully ingested and profiled! (Status: {ds_resp.get('processing_status')})")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Dataset ingestion failed: {exc}")
                        else:
                            # In-process ingestion
                            try:
                                df = load_tabular_file(file_bytes, filename)
                                in_proc_db.drop_all_tables()
                                schema = in_proc_db.load_dataframe(df, filename.rsplit(".", 1)[0])
                                prof = profiler.profile(df, dataset_name=filename)
                                st.session_state.tables[filename] = schema
                                st.session_state.profiles[filename] = prof
                                st.session_state.active_table = filename
                                st.session_state.active_dataset_name = filename
                                st.success(f"Ingested '{filename}' ({prof.row_count} rows, {prof.column_count} cols) in-process!")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"In-process parsing failed: {exc}")

        st.markdown("#### Datasets in Project")
        active_pid = st.session_state.active_project_id
        datasets = st.session_state.dataset_cache.get(active_pid, []) if active_pid else []

        if not datasets and not st.session_state.tables:
            st.caption("No datasets uploaded to this project yet. Upload a CSV or click 'Load Sample Sales Data' in the sidebar.")
        elif datasets:
            for d in datasets:
                with st.container():
                    c_badge = "dp-chip-success" if d.get("processing_status") == "READY" else "dp-chip-info"
                    st.markdown(
                        f"""
                        <div class="dp-card">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <strong>{d.get('filename')}</strong>
                                <span class="dp-chip {c_badge}">{d.get('processing_status', 'UNKNOWN')}</span>
                            </div>
                            <div style="font-size: 0.8rem; color: var(--dp-text-muted); margin-top: 0.4rem;">
                                Rows: {d.get('row_count', 'N/A')} | Columns: {d.get('column_count', 'N/A')} | Size: {d.get('file_size', 0):,} bytes
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
        elif st.session_state.tables:
            for tname, tschema in st.session_state.tables.items():
                st.markdown(
                    f"""
                    <div class="dp-card">
                        <strong>{tname}</strong> (In-Process)
                        <div style="font-size: 0.8rem; color: var(--dp-text-muted); margin-top: 0.2rem;">
                            Rows: {tschema.row_count} | Columns: {len(tschema.columns)}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# =============================================================================
# TAB 2: Dataset Profile & Semantic Schema View
# =============================================================================
with tab_profile:
    st.markdown("### 📊 Dataset Profile & Semantic Schema")
    active_did = st.session_state.active_dataset_id
    active_dname = st.session_state.active_dataset_name

    profile_data: dict[str, Any] = {}
    semantic_data: dict[str, Any] = {}

    if active_did and st.session_state.api_mode and backend_online:
        try:
            p_resp = api_client.get_dataset_profile(active_did)
            profile_data = p_resp.get("profile", {})
            semantic_data = p_resp.get("semantic_schema", {})
        except Exception as exc:
            st.warning(f"Could not fetch profile from backend: {exc}")
    elif st.session_state.active_table and st.session_state.active_table in st.session_state.profiles:
        in_p = st.session_state.profiles[st.session_state.active_table]
        profile_data = in_p.to_dict()

    if not profile_data and not semantic_data:
        st.info("No active dataset profile available. Upload a dataset in the Projects tab or load sample data from the sidebar.")
    else:
        # Overview metric cards
        row_cnt = profile_data.get("row_count", semantic_data.get("row_count", 0))
        col_cnt = profile_data.get("column_count", semantic_data.get("column_count", 0))
        pk_cand = semantic_data.get("primary_key_candidate", "None detected")
        targets = semantic_data.get("target_variable_candidates", [])

        col_p1, col_p2, col_p3, col_p4 = st.columns(4)
        col_p1.metric("Total Records", f"{row_cnt:,}")
        col_p2.metric("Total Columns", col_cnt)
        col_p3.metric("Primary Key", pk_cand or "None")
        col_p4.metric("Targets", ", ".join(targets) if targets else "None")

        st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)

        prof_tab1, prof_tab2, prof_tab3, prof_tab4 = st.tabs([
            "Semantic Schema",
            "Data Quality Assessment",
            "Descriptive Statistics",
            "Sample Rows",
        ])

        with prof_tab1:
            st.markdown("#### Inferred Semantic Schema & Column Metadata")
            sem_cols = semantic_data.get("columns", [])
            if sem_cols:
                disp_cols = []
                for sc in sem_cols:
                    disp_cols.append({
                        "Column": sc.get("name"),
                        "Semantic Type": sc.get("semantic_type"),
                        "Physical Type": sc.get("physical_type"),
                        "Nullable": "Yes" if sc.get("nullable") else "No",
                        "Role / Identifier": "Primary Key / ID" if sc.get("is_identifier") else ("Potential Target" if sc.get("is_potential_target") else "Feature"),
                        "Description": sc.get("description", ""),
                    })
                st.dataframe(pd.DataFrame(disp_cols), use_container_width=True)
            elif profile_data.get("columns"):
                disp_cols = []
                for c in profile_data["columns"]:
                    disp_cols.append({
                        "Column": c.get("name"),
                        "Inferred Type": c.get("inferred_type"),
                        "Physical Dtype": c.get("dtype"),
                        "Null Count": c.get("null_count", 0),
                        "Null %": f"{c.get('null_pct', 0):.1f}%",
                        "Distinct Values": c.get("distinct_count", 0),
                    })
                st.dataframe(pd.DataFrame(disp_cols), use_container_width=True)
            else:
                st.caption("No column metadata available.")

        with prof_tab2:
            st.markdown("#### Data Quality Warnings & Integrity Checks")
            warnings = profile_data.get("warnings", [])
            if warnings:
                for w in warnings:
                    sev = w.get("severity", "info")
                    badge_cls = f"dp-chip dp-chip-{sev}"
                    st.markdown(
                        f'<span class="{badge_cls}">{sev.upper()}</span> **{w.get("column", "dataset")}**: {w.get("message")}',
                        unsafe_allow_html=True,
                    )
            else:
                st.success("All deterministic data quality checks passed. Zero integrity anomalies detected.")

        with prof_tab3:
            st.markdown("#### Numeric Distribution Parameters")
            stat_rows = []
            for c in profile_data.get("columns", []):
                if c.get("mean") is not None:
                    stat_rows.append({
                        "Column": c.get("name"),
                        "Mean": round(c.get("mean"), 2),
                        "Min": round(c.get("min"), 2) if c.get("min") is not None else None,
                        "Median": round(c.get("median"), 2) if c.get("median") is not None else None,
                        "Max": round(c.get("max"), 2) if c.get("max") is not None else None,
                        "Std Dev": round(c.get("std"), 2) if c.get("std") is not None else None,
                        "Outliers (>1.5 IQR)": c.get("outlier_count", 0),
                    })
            if stat_rows:
                st.dataframe(pd.DataFrame(stat_rows), use_container_width=True)
            else:
                st.caption("No continuous numeric columns available for distribution summary.")

        with prof_tab4:
            st.markdown("#### Raw Data Sample Preview")
            if profile_data.get("columns") and profile_data["columns"][0].get("sample_values"):
                samples_dict = {c["name"]: c.get("sample_values", []) for c in profile_data["columns"]}
                max_len = max(len(v) for v in samples_dict.values())
                padded = {k: v + [None] * (max_len - len(v)) for k, v in samples_dict.items()}
                st.dataframe(pd.DataFrame(padded), use_container_width=True)
            elif in_proc_db and st.session_state.active_table:
                try:
                    sample_df = in_proc_db.query(f'SELECT * FROM "{st.session_state.active_table}" LIMIT 25')
                    st.dataframe(sample_df, use_container_width=True)
                except Exception as exc:
                    st.caption(f"Preview unavailable: {exc}")
            else:
                st.caption("Raw sample preview unavailable.")


# =============================================================================
# TAB 3: AI Analyst Workspace
# =============================================================================
with tab_analyst:
    st.markdown("### 🤖 AI Analyst Workspace")
    st.caption("Ask natural-language analytical questions. VERIDEX plans the query, validates read-only AST safety, self-corrects SQL errors, and grounds insights in executed results.")

    active_did = st.session_state.active_dataset_id
    active_dname = st.session_state.active_dataset_name

    def _set_analyst_question(q_text: str):
        st.session_state["question_input"] = q_text

    # Quick Queries Chips
    st.caption("Quick Queries:")
    chip_cols = st.columns(min(len(EXAMPLE_QUESTIONS[:4]), 4))
    for idx, ex_q in enumerate(EXAMPLE_QUESTIONS[:4]):
        with chip_cols[idx]:
            st.button(ex_q, key=f"starter_chip_{idx}", on_click=_set_analyst_question, args=(ex_q,), use_container_width=True)

    with st.form("ai_analyst_form", clear_on_submit=False):
        col_q, col_btn = st.columns([5, 1])
        with col_q:
            query_input = st.text_input(
                "Question",
                key="question_input",
                placeholder="e.g. What is the total revenue by region?",
                label_visibility="collapsed",
            )
        with col_btn:
            submit_analysis = st.form_submit_button("Analyze", type="primary", use_container_width=True)

    current_q = st.session_state.get("question_input", "").strip()

    if submit_analysis and not current_q:
        st.warning("Please enter an analytical question.")
    elif submit_analysis and current_q:
        t0 = time.time()
        with st.spinner("VERIDEX is planning query, validating SQL safety, and executing..."):
            if st.session_state.api_mode and backend_online and active_did:
                try:
                    resp = api_client.analyze_dataset(active_did, current_q)
                    sql_resp = api_client.get_analysis_sql(resp["analysis_id"]) if resp.get("analysis_id") else {}
                    res_resp = api_client.get_analysis_results(resp["analysis_id"]) if resp.get("analysis_id") else {}

                    analysis_res = {
                        "analysis_id": resp.get("analysis_id"),
                        "question": resp.get("question", current_q),
                        "success": resp.get("success", False),
                        "insight": resp.get("insight", "No insight returned."),
                        "sql": sql_resp.get("sql"),
                        "chart_type": resp.get("chart_type") or res_resp.get("chart_type"),
                        "metrics": resp.get("metrics") or res_resp.get("metrics", {}),
                        "result_preview": res_resp.get("result_preview", []),
                        "retry_count": resp.get("retry_count", 0),
                        "sql_execution_time_ms": resp.get("sql_execution_time_ms", 0),
                        "total_latency_ms": resp.get("total_latency_ms", int((time.time() - t0) * 1000)),
                        "correction_history": sql_resp.get("correction_history", []),
                        "error": resp.get("error"),
                    }
                    st.session_state.last_result = analysis_res
                    st.session_state.analysis_history.append(analysis_res)
                except Exception as exc:
                    st.error(f"Analysis failed: {exc}")
            else:
                # In-process fallback execution
                in_tbl = st.session_state.active_table or "dataset"
                in_prof = st.session_state.profiles.get(in_tbl)
                res = in_proc_orchestrator.analyze(current_q, data_profile=in_prof)
                analysis_res = {
                    "question": res.question,
                    "success": res.success,
                    "insight": res.insight,
                    "sql": res.sql,
                    "chart_type": res.chart_type,
                    "metrics": res.metrics,
                    "result_preview": res.result_preview,
                    "retry_count": res.retry_count,
                    "sql_execution_time_ms": res.sql_execution_time_ms,
                    "total_latency_ms": res.total_latency_ms or int((time.time() - t0) * 1000),
                    "correction_history": res.correction_history,
                    "error": res.error,
                }
                st.session_state.last_result = analysis_res
                st.session_state.analysis_history.append(analysis_res)

    # -------------------------------------------------------------------------
    # Render Analysis Results
    # -------------------------------------------------------------------------
    current_result = st.session_state.last_result
    if current_result is not None:
        if not current_result.get("success"):
            st.error(f"Query execution could not be completed: {current_result.get('error')}")
        else:
            # 1. Grounded Analytical Insight
            st.markdown(
                f"""
                <div class="dp-insight-box">
                    <strong>Grounded Insight:</strong> {current_result.get('insight')}
                </div>
                """,
                unsafe_allow_html=True,
            )

            # 2. Key Aggregate Metrics
            metrics_dict = current_result.get("metrics", {})
            if metrics_dict:
                num_items = {k: v for k, v in metrics_dict.items() if isinstance(v, (int, float, str))}
                if num_items:
                    m_cols = st.columns(min(len(num_items), 4))
                    for idx, (m_k, m_v) in enumerate(list(num_items.items())[:4]):
                        val_str = f"{m_v:,.2f}" if isinstance(m_v, float) else str(m_v)
                        with m_cols[idx % 4]:
                            st.metric(m_k.replace("_", " ").title(), val_str)

            # 3. Visuals, Data, and Technical SQL Audit Tabs
            res_tab_chart, res_tab_table, res_tab_sql = st.tabs([
                "Interactive Chart",
                "Tabular Preview",
                "Verified SQL & Audit Trail",
            ])

            with res_tab_chart:
                chart_t = current_result.get("chart_type")
                rows = current_result.get("result_preview", [])
                if rows and chart_t and chart_t != "table":
                    # Adapt to AnalysisResult structure for build_chart
                    dummy_ar = AnalysisResult(
                        question=current_result.get("question", ""),
                        success=True,
                        result_preview=rows,
                        chart_type=chart_t,
                    )
                    fig = build_chart(dummy_ar, dark_mode=True)
                    if fig is not None:
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.caption(f"No chart visualizer suitable for chart type '{chart_t}'. Inspect the tabular preview below.")
                else:
                    st.caption("Answer is best represented as a tabular summary or aggregate scalar.")

            with res_tab_table:
                rows = current_result.get("result_preview", [])
                if rows:
                    df_preview = pd.DataFrame(rows)
                    st.dataframe(df_preview, use_container_width=True)
                    st.download_button(
                        "Download Results (CSV)",
                        df_preview.to_csv(index=False),
                        file_name="veridex_query_result.csv",
                        mime="text/csv",
                    )
                else:
                    st.caption("No tabular records returned.")

            with res_tab_sql:
                st.markdown("##### Executed SQL Statement")
                sql_text = current_result.get("sql")
                if sql_text:
                    st.code(sql_text, language="sql")
                else:
                    st.caption("No SQL was executed (computed directly from profiling metadata).")

                col_a1, col_a2, col_a3 = st.columns(3)
                col_a1.caption("**Status:** Validated Read-Only SELECT")
                col_a2.caption(f"**Execution Runtime:** {current_result.get('sql_execution_time_ms', 0)} ms")
                col_a3.caption(f"**Self-Correction Retries:** {current_result.get('retry_count', 0)}")

                corr_hist = current_result.get("correction_history", [])
                if corr_hist:
                    st.markdown("###### SQL Self-Correction History")
                    for attempt in corr_hist:
                        st.caption(f"- **Attempt {attempt.get('attempt')}:** Failed with `{attempt.get('error')}`")
                        if attempt.get("fixed_sql"):
                            st.code(attempt.get("fixed_sql"), language="sql")

    # -------------------------------------------------------------------------
    # Analysis History Section
    # -------------------------------------------------------------------------
    if st.session_state.analysis_history:
        st.markdown("---")
        st.markdown("#### Previous Analyses in Session")
        for h_idx, past_run in enumerate(reversed(st.session_state.analysis_history[-5:])):
            with st.expander(f"Question: {past_run.get('question')} ({past_run.get('total_latency_ms', 0)} ms)"):
                st.markdown(f"**Insight:** {past_run.get('insight')}")
                if past_run.get("sql"):
                    st.code(past_run.get("sql"), language="sql")
                if st.button("Load this analysis", key=f"load_past_{h_idx}"):
                    st.session_state.last_result = past_run
                    st.rerun()


# =============================================================================
# TAB 4: Grounded Analytical Reports
# =============================================================================
with tab_reports:
    st.markdown("### 📑 Grounded 10-Section Analytical Reports")
    st.caption("Compile publication-ready, fully grounded analytical reports. Deterministically synthesizes profiling parameters, data quality findings, and verified SQL query results.")

    active_did = st.session_state.active_dataset_id
    active_pid = st.session_state.active_project_id
    active_dname = st.session_state.active_dataset_name

    col_r1, col_r2 = st.columns([1, 1.4], gap="large")

    with col_r1:
        st.markdown("#### Compile New Report")
        if not active_did:
            st.info("Select or upload a dataset in the Projects tab to compile an analytical report.")
        else:
            rep_title_input = st.text_input(
                "Report Title",
                value=f"Analytical Report - {active_dname or 'Dataset'}",
            )

            if st.button("Generate Analytical Report", type="primary", use_container_width=True):
                with st.spinner("Compiling deterministic 10-section grounded analytical report..."):
                    if st.session_state.api_mode and backend_online:
                        try:
                            # Gather recent analysis IDs
                            recent_aids = [
                                r["analysis_id"]
                                for r in st.session_state.analysis_history
                                if r.get("analysis_id")
                            ]
                            new_rep = api_client.create_report(
                                dataset_id=active_did,
                                project_id=active_pid,
                                title=rep_title_input,
                                analysis_ids=recent_aids,
                            )
                            st.session_state.current_report = new_rep
                            st.success(f"Analytical Report '{new_rep.get('title')}' successfully compiled!")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Failed to generate report via API: {exc}")
                    else:
                        # In-process fallback
                        st.info("Generating report in-process...")
                        if st.session_state.last_result:
                            md_rep = render_markdown_report(AnalysisResult(
                                question=st.session_state.last_result.get("question", "Analysis"),
                                success=True,
                                insight=st.session_state.last_result.get("insight", ""),
                                sql=st.session_state.last_result.get("sql"),
                                metrics=st.session_state.last_result.get("metrics", {}),
                                result_preview=st.session_state.last_result.get("result_preview", []),
                            ))
                            st.session_state.current_report = {
                                "id": f"rep-{int(time.time())}",
                                "title": rep_title_input,
                                "markdown": md_rep,
                            }
                            st.success("In-process report generated!")
                            st.rerun()

    with col_r2:
        st.markdown("#### Previously Generated Reports")
        if st.session_state.api_mode and backend_online and active_pid:
            try:
                reports_list = api_client.list_reports(project_id=active_pid)
                if reports_list:
                    for rep in reports_list:
                        with st.container():
                            st.markdown(
                                f"""
                                <div class="dp-card">
                                    <strong>{rep.get('title')}</strong>
                                    <div style="font-size: 0.8rem; color: var(--dp-text-muted);">
                                        ID: `{rep.get('id')}` | Generated: {rep.get('created_at', 'N/A')}
                                    </div>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )
                            if st.button("Inspect Report", key=f"view_rep_{rep.get('id')}"):
                                full_rep = api_client.get_report(rep["id"])
                                st.session_state.current_report = full_rep
                                st.rerun()
                else:
                    st.caption("No reports compiled for this project yet.")
            except Exception as exc:
                st.caption(f"Could not load previous reports: {exc}")
        else:
            st.caption("Reports list available in REST API mode.")

    # Display Active Report
    cur_rep = st.session_state.get("current_report")
    if cur_rep:
        st.markdown("---")
        st.markdown(f"### {cur_rep.get('title', 'Analytical Report')}")

        md_content = cur_rep.get("markdown")
        if md_content:
            col_d1, col_d2 = st.columns([1, 1])
            with col_d1:
                st.download_button(
                    "Download Markdown Report",
                    data=md_content,
                    file_name=f"{cur_rep.get('id', 'veridex_report')}.md",
                    mime="text/markdown",
                )
            with col_d2:
                if cur_rep.get("content"):
                    st.download_button(
                        "Download JSON Report",
                        data=json.dumps(cur_rep["content"], indent=2),
                        file_name=f"{cur_rep.get('id', 'veridex_report')}.json",
                        mime="application/json",
                    )

            st.markdown(md_content)


# =============================================================================
# TAB 5: Operational Observability (Admin & Dev Telemetry)
# =============================================================================
with tab_observability:
    st.markdown("### 📈 Operational Observability & Telemetry")
    st.caption("Real-time operational metrics exposed via `GET /metrics` and `GET /ready`. Monitored latencies (p50/p90/p99), error rates, and query repair counts.")

    if not st.session_state.api_mode or not backend_online:
        st.warning("Observability dashboard requires an active connection to the VERIDEX REST API backend. Start the backend with `uvicorn app.api.main:app` and enable API mode in the sidebar.")
    else:
        try:
            m_data = api_client.metrics()
            r_data = api_client.ready()
            metrics = m_data.get("metrics", {})

            # Top telemetry cards
            col_o1, col_o2, col_o3, col_o4, col_o5 = st.columns(5)
            col_o1.metric("Total Requests", metrics.get("total_requests", 0))
            col_o2.metric("Total Errors", metrics.get("total_errors", 0))
            col_o3.metric("Error Rate", f"{metrics.get('error_rate', 0.0) * 100:.2f}%")
            col_o4.metric("Active Analyses", metrics.get("active_analyses", 0))
            col_o5.metric("SQL Repairs", metrics.get("sql_repair_count", 0))

            st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)

            # Latency percentiles
            lat = metrics.get("latency_ms", {})
            st.markdown("#### Latency Percentiles (Sliding Window)")
            col_l1, col_l2, col_l3, col_l4, col_l5 = st.columns(5)
            col_l1.metric("p50 Latency", f"{lat.get('p50', 0):.1f} ms")
            col_l2.metric("p90 Latency", f"{lat.get('p90', 0):.1f} ms")
            col_l3.metric("p99 Latency", f"{lat.get('p99', 0):.1f} ms")
            col_l4.metric("Avg Latency", f"{lat.get('avg', 0):.1f} ms")
            col_l5.metric("Min / Max", f"{lat.get('min', 0):.0f} / {lat.get('max', 0):.0f} ms")

            st.markdown("---")

            col_sub1, col_sub2 = st.columns(2)

            with col_sub1:
                st.markdown("#### HTTP Status Codes")
                sc = metrics.get("status_codes", {})
                if sc:
                    sc_df = pd.DataFrame([{"Status Code": k, "Requests": v} for k, v in sc.items()])
                    st.dataframe(sc_df, use_container_width=True)
                else:
                    st.caption("No HTTP status code records accumulated.")

            with col_sub2:
                st.markdown("#### Requests by Endpoint")
                endpoints = metrics.get("requests_by_endpoint", {})
                if endpoints:
                    ep_df = pd.DataFrame([{"Endpoint": k, "Call Count": v} for k, v in endpoints.items()])
                    st.dataframe(ep_df, use_container_width=True)
                else:
                    st.caption("No endpoint traffic accumulated.")

            st.markdown("---")
            st.caption(f"Platform Uptime: {metrics.get('uptime_seconds', 0):.1f} seconds | Database: {r_data.get('database', {}).get('status', 'N/A')} | Storage: {r_data.get('storage', {}).get('status', 'N/A')}")

            if st.button("Refresh Telemetry", use_container_width=True):
                st.rerun()

        except Exception as exc:
            st.error(f"Could not retrieve telemetry from API: {exc}")
