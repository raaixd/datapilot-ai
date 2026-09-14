"""
DataPilot AI -- Streamlit dashboard.

NOTE ON TESTING: this file requires `streamlit` and `plotly`, neither of
which are installed in the sandbox this project was developed in (no
network access to pip install them here). It was written directly against
the app/agents/orchestrator.py core (which IS tested -- see tests/) and
syntax-checked with `python -m py_compile`, but has NOT actually been run
with `streamlit run` in this environment. Run it locally:

    pip install -r requirements.txt
    streamlit run frontend/streamlit_app.py

and confirm the workflow end-to-end before treating it as verified.

This talks directly to the orchestrator in-process -- it does NOT call the
FastAPI backend over HTTP, so it works standalone with just
`streamlit run frontend/streamlit_app.py` (no need to also start
`uvicorn`). The two services share the same core code but run
independently; see README.md's "Running it" / "How the API and frontend
relate" section.
"""
from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from app.agents.orchestrator import Orchestrator
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.data.database import AnalyticalDatabase
from app.data.loader import load_tabular_file
from app.data.profiler import DataProfiler
from app.llm.factory import build_llm_client
from app.reports.markdown_report import render_markdown_report
from app.reports.pdf_report import render_pdf_report
from app.visualization.charts import build_chart

configure_logging()
logger = logging.getLogger(__name__)
st.set_page_config(page_title="DataPilot AI", page_icon="\U0001F4CA", layout="wide")

EXAMPLE_QUESTIONS = [
    "What is the total revenue?",
    "What are the top 5 regions by revenue?",
    "Show me the monthly revenue trend",
    "Compare revenue between regions",
    "Which products experienced declining sales?",
    "How much data is missing?",
]


@st.cache_resource
def _get_shared_resources():
    """Process-wide singletons that hold NO user data and are safe to share
    across every session on this Streamlit server: settings, the LLM client
    (stateless -- it just calls an API/runs local rules), and the profiler
    (stateless -- pure functions over whatever DataFrame it's given).
    `st.cache_resource` is explicitly a GLOBAL cache shared by every user
    connected to this server process -- so nothing session-specific (the
    loaded dataset, the database connection) may live here. See
    `_get_session_backend()` below for what must NOT be cached this way,
    and README "Multi-user and session isolation" for the full writeup."""
    settings = get_settings()
    try:
        llm = build_llm_client(settings)
    except Exception as exc:
        st.error(
            f"Could not initialize the LLM client for LLM_PROVIDER={settings.llm_provider}: {exc}\n\n"
            "Check your .env file against .env.example, or set LLM_PROVIDER=mock to run without any API key."
        )
        st.stop()
    return llm, DataProfiler(), settings


def _get_session_backend():
    """Per-session database + orchestrator, stored in `st.session_state`
    (which IS session-isolated by Streamlit -- unlike `st.cache_resource`).
    Uses `AnalyticalDatabase.create_session_database()`: a thread-safe,
    file-backed temp database with a unique path per session, so this
    session's data cannot leak into, or be overwritten by, another
    session's (see app/data/database.py's module docstring and
    tests/test_database_thread_safety.py for the underlying guarantee)."""
    if "session_db" not in st.session_state:
        llm, _profiler, settings = _get_shared_resources()
        db = AnalyticalDatabase.create_session_database(backend=settings.database_backend)
        orchestrator = Orchestrator(db, llm, max_result_rows=settings.max_result_rows, settings=settings)
        st.session_state.session_db = db
        st.session_state.session_orchestrator = orchestrator
        logger.info("Created new session database for this Streamlit session: %s", db.path)
    return st.session_state.session_db, st.session_state.session_orchestrator


_llm, profiler, settings = _get_shared_resources()
db, orchestrator = _get_session_backend()

for key, default in [("history", []), ("profile", None), ("table_name", None), ("last_result", None)]:
    if key not in st.session_state:
        st.session_state[key] = default

st.title("\U0001F4CA DataPilot AI")
st.caption(
    "Ask business questions about your data in plain English and get back the SQL used, "
    "computed metrics, a chart, and an exportable report."
)
if settings.llm_provider == "mock":
    st.info(
        "Running with **LLM_PROVIDER=mock** -- a deterministic, rule-based stand-in for a language "
        "model (no API key needed, no network calls made). Set LLM_PROVIDER=anthropic or "
        "LLM_PROVIDER=ollama in your .env to use a real model.",
        icon="\u2139\ufe0f",
    )

with st.sidebar:
    st.header("1. Load a dataset")
    uploaded = st.file_uploader("Upload a CSV or Excel file", type=["csv", "xlsx", "xls"])
    use_sample = st.button("Use sample sales dataset", use_container_width=True)

    if uploaded is not None:
        try:
            df = load_tabular_file(uploaded, uploaded.name)
        except Exception as exc:
            st.error(f"Could not load '{uploaded.name}': {exc}")
            df = None
        if df is not None:
            table_name = uploaded.name.rsplit(".", 1)[0]
            st.session_state.profile = profiler.profile(df, dataset_name=uploaded.name)
            schema = db.load_dataframe(df, table_name)
            st.session_state.table_name = schema.name
            st.session_state.last_result = None
            st.success(f"Loaded '{schema.name}' -- {schema.row_count} rows.")

    if use_sample:
        df = pd.read_csv("data/sample_sales.csv")
        st.session_state.profile = profiler.profile(df, dataset_name="sample_sales.csv")
        schema = db.load_dataframe(df, "sample_sales")
        st.session_state.table_name = schema.name
        st.session_state.last_result = None
        st.success(f"Loaded sample dataset -- {schema.row_count} rows.")

    if st.session_state.history:
        st.header("Query history")
        for past in reversed(st.session_state.history[-10:]):
            st.caption(f"\u2022 {past}")

if st.session_state.profile is None:
    st.info(
        "\U0001F446 Upload a CSV/Excel file or click **'Use sample sales dataset'** in the sidebar to get started.\n\n"
        "New here? Try one of these once a dataset is loaded:\n" + "\n".join(f"- {q}" for q in EXAMPLE_QUESTIONS)
    )
else:
    profile = st.session_state.profile

    with st.expander("Dataset preview", expanded=False):
        try:
            preview_df = db.query(f'SELECT * FROM "{st.session_state.table_name}" LIMIT 20', max_rows=20)
            st.dataframe(preview_df, use_container_width=True)
        except Exception as exc:
            st.caption(f"Preview unavailable: {exc}")

    st.subheader("Data quality summary")
    col1, col2, col3 = st.columns(3)
    col1.metric("Rows", profile.row_count)
    col2.metric("Columns", profile.column_count)
    col3.metric("Duplicate rows", profile.duplicate_row_count)

    with st.expander("Column details", expanded=False):
        st.dataframe(pd.DataFrame([vars(c) for c in profile.columns]), use_container_width=True)

    if profile.warnings:
        with st.expander(f"\u26a0\ufe0f {len(profile.warnings)} data quality warning(s)", expanded=profile.has_critical_warnings):
            for w in profile.warnings:
                icon = {"critical": "\U0001F534", "warning": "\U0001F7E0", "info": "\U0001F535"}.get(w.severity, "\u2022")
                st.write(f"{icon} **{w.column}** -- {w.message}")
    else:
        st.success("No data quality issues detected.")

    st.subheader("Ask a question")

    # -- Question input, rewritten (round 3) to fix a reported bug: the
    # "Type a question first..." warning could appear even after typing a
    # valid question. ROOT CAUSE ANALYSIS: this file requires `streamlit`,
    # which is NOT installed in this project's dev sandbox (no network
    # access -- see README "What has and hasn't been executed"), so I could
    # not run this app and watch the bug happen live. I traced the code
    # instead and found a specific, well-documented Streamlit anti-pattern:
    # the previous version gave `st.text_input(...)` an inline
    # `value=st.session_state.pop("_pending_question", "")` expression but
    # NO explicit `key=`. Streamlit's widgets are only guaranteed to
    # reliably preserve user-typed edits across reruns when they have a
    # STABLE, explicit key and are not simultaneously fed a `value=`
    # expression recomputed from other state on every script run -- mixing
    # those two is exactly the pattern Streamlit's own docs warn causes
    # inconsistent/reset widget values. The fix below uses the documented-
    # safe alternative: a stable `key="question_input"`, and "fill this
    # question in" (example buttons, follow-up buttons) is done via
    # `on_click` CALLBACKS that write directly to
    # `st.session_state["question_input"]` -- callbacks run and finish
    # BEFORE the script body reruns and re-renders the widget, so there is
    # no `value=` vs. typed-text race at all. The "Analyze" button also
    # reads `st.session_state["question_input"]` directly rather than a
    # local variable, removing any possibility of using a stale copy.
    #
    # This has NOT been confirmed against a live run of this exact file
    # (see the note above) -- if the symptom recurs after this fix, enable
    # "Show debug info" below and check whether `question_input` in the
    # session-state snapshot matches what's actually in the input box; that
    # will immediately show whether this widget-state issue or something
    # else (e.g. a stale `orchestrator`/`profile` reference) is at fault.

    def _use_question(q: str) -> None:
        st.session_state["question_input"] = q

    st.session_state.setdefault("question_input", "")

    with st.expander("Example questions"):
        cols = st.columns(2)
        for i, q in enumerate(EXAMPLE_QUESTIONS):
            with cols[i % 2]:
                st.button(q, key=f"example_{q}", on_click=_use_question, args=(q,), use_container_width=True)

    st.text_input(
        "Business question", key="question_input",
        placeholder="What is the total revenue by product category?",
    )
    debug_mode = st.checkbox("Show debug info", value=False, help="Developer diagnostics -- not for normal use.")
    ask = st.button("Analyze", type="primary")

    if debug_mode:
        with st.expander("Debug: session state snapshot", expanded=True):
            st.json({
                "question_input": st.session_state.get("question_input"),
                "ask_clicked_this_run": ask,
                "history_length": len(st.session_state.history),
                "has_last_result": st.session_state.last_result is not None,
            })

    current_question = st.session_state.get("question_input", "")

    if ask and not current_question.strip():
        st.warning("Type a question first, or click one of the example questions above.")

    if ask and current_question.strip():
        with st.spinner("Analyzing..."):
            result = orchestrator.analyze(current_question, data_profile=profile)
        st.session_state.history.append(current_question)
        st.session_state.last_result = result
        if debug_mode:
            logger.info("Analyzed question=%r -> success=%s scope=%s", current_question, result.success, result.scope)

    result = st.session_state.last_result
    if result is not None:
        if not result.success:
            st.error(f"**Could not answer this question.** {result.error}")
            if result.plan and result.plan.ambiguous_options:
                st.write("This question could mean:")
                for opt in result.plan.ambiguous_options:
                    st.write(f"- {opt}")
        else:
            st.markdown("### Executive summary")
            st.write(result.insight)
            if result.llm_provider == "mock":
                st.caption("Generated by the deterministic mock LLM (no live model call was made).")

            tab_chart, tab_sql, tab_data, tab_report = st.tabs(["Chart", "SQL", "Data", "Export"])

            with tab_chart:
                if result.sql is None:
                    st.info("This question was answered directly from the data-quality profile; there's no query result to chart.")
                else:
                    fig = build_chart(result)
                    if fig is not None:
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("No chart-friendly shape was found for this result; see the Data tab.")

            with tab_sql:
                if result.sql:
                    st.code(result.sql, language="sql")
                    for w in result.validation_warnings:
                        st.caption(f"\u2139\ufe0f {w}")
                else:
                    st.caption("No SQL was generated -- this question was answered directly from the dataset profile.")

            with tab_data:
                data_df = pd.DataFrame(result.result_preview)
                st.dataframe(data_df, use_container_width=True)
                if not data_df.empty:
                    st.download_button(
                        "Download result as CSV", data_df.to_csv(index=False),
                        file_name="datapilot_result.csv", mime="text/csv",
                    )
                if result.metrics:
                    st.json(result.metrics)

            with tab_report:
                report_md = render_markdown_report(result)
                st.download_button("Download Markdown report", report_md, file_name="datapilot_report.md")
                try:
                    import io
                    pdf_buffer = io.BytesIO()
                    render_pdf_report(result, "/tmp/_datapilot_report.pdf")
                    with open("/tmp/_datapilot_report.pdf", "rb") as f:
                        st.download_button("Download PDF report", f.read(), file_name="datapilot_report.pdf", mime="application/pdf")
                except Exception as exc:
                    st.caption(f"PDF export unavailable: {exc}")
                st.markdown(report_md)

            if result.follow_up_questions:
                st.markdown("**Follow-up questions:**")
                for q in result.follow_up_questions:
                    st.button(q, key=f"followup_{q}", on_click=_use_question, args=(q,))
