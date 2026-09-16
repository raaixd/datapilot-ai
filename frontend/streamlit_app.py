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

# -- Visual polish: a small design-token system (CSS variables), a hero
# banner, and severity badges. This is CSS/layout only -- no behavior
# change -- and, like the rest of this file, has NOT been visually verified
# against a live `streamlit run` (streamlit isn't installed in this
# project's dev sandbox; see the NOTE at the top of this file).
#
# Card-style sections (dataset preview, data-quality summary, results) use
# `st.container(border=True)` instead of hand-rolled CSS targeting
# Streamlit's internal `data-testid` selectors wherever possible --
# `border=True` is a stable, documented public API (Streamlit >= 1.31,
# already the floor pinned in requirements.txt), whereas internal class
# names are not part of Streamlit's public contract and can silently break
# on a version bump. The CSS below is reserved for things Streamlit has no
# public styling API for (typography, the hero banner, severity badges).
st.markdown(
    """
    <style>
    :root {
        --dp-primary: #4F46E5;
        --dp-primary-soft: rgba(79, 70, 229, 0.08);
        --dp-border: rgba(120, 120, 120, 0.18);
        --dp-radius: 12px;
        --dp-critical: #DC2626;
        --dp-warning: #D97706;
        --dp-info: #2563EB;
    }
    .block-container { padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1140px; }
    h2, h3 { font-weight: 600; margin-top: 1.4rem; }

    .dp-hero {
        background: linear-gradient(135deg, var(--dp-primary-soft), rgba(79, 70, 229, 0.02));
        border: 1px solid var(--dp-border);
        border-radius: var(--dp-radius);
        padding: 1.6rem 1.8rem;
        margin-bottom: 1.2rem;
    }
    .dp-hero h1 { font-weight: 800; letter-spacing: -0.03em; margin: 0 0 0.3rem 0; font-size: 2.1rem; }
    .dp-hero p { margin: 0; opacity: 0.8; font-size: 1.02rem; }

    div[data-testid="stMetric"] {
        background-color: rgba(120, 120, 120, 0.06);
        border: 1px solid var(--dp-border);
        border-radius: 10px;
        padding: 0.9rem 1rem 0.6rem 1rem;
    }
    div[data-testid="stButton"] > button { border-radius: 8px; }
    div[data-testid="stButton"] > button[kind="primary"] { font-weight: 600; }
    .stTabs [data-baseweb="tab-list"] { gap: 4px; }
    .stTabs [data-baseweb="tab"] { border-radius: 8px 8px 0 0; padding: 0.5rem 1rem; }
    code { border-radius: 6px; }

    .dp-badge {
        display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
        font-size: 0.78rem; font-weight: 600; margin-right: 0.4rem;
    }
    .dp-badge-critical { background: rgba(220, 38, 38, 0.12); color: var(--dp-critical); }
    .dp-badge-warning  { background: rgba(217, 119, 6, 0.12); color: var(--dp-warning); }
    .dp-badge-info     { background: rgba(37, 99, 235, 0.12); color: var(--dp-info); }
    </style>
    """,
    unsafe_allow_html=True,
)


def _badge(severity: str) -> str:
    cls = {"critical": "dp-badge-critical", "warning": "dp-badge-warning", "info": "dp-badge-info"}.get(severity, "dp-badge-info")
    return f'<span class="dp-badge {cls}">{severity.upper()}</span>'


EXAMPLE_QUESTIONS = [
    "What is the total revenue?",
    "What are the top 5 regions by revenue?",
    "Show me the monthly revenue trend",
    "Compare revenue between regions",
    "Which products experienced declining sales?",
    "Are there any anomalies in revenue?",
    "How much data is missing?",
]

SCOPE_ICON = {"out_of_scope": "\U0001F4AC", "ambiguous": "\U0001F914", "unsafe": "\U0001F6D1", "in_scope": "\u26A0\ufe0f"}


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

st.markdown(
    """
    <div class="dp-hero">
        <h1>\U0001F4CA DataPilot AI</h1>
        <p>Ask business questions about your data in plain English and get back the SQL used,
        computed metrics, a chart, and an exportable report.</p>
    </div>
    """,
    unsafe_allow_html=True,
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
    if st.session_state.table_name:
        st.caption(f"\U0001F4C1 Currently loaded: **{st.session_state.table_name}**")

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
            db.drop_all_tables()  # this app holds one active dataset per session -- see README "Known limitations"
            schema = db.load_dataframe(df, table_name)
            st.session_state.table_name = schema.name
            st.session_state.last_result = None
            st.success(f"Loaded '{schema.name}' -- {schema.row_count} rows.")

    if use_sample:
        df = pd.read_csv("data/sample_sales.csv")
        st.session_state.profile = profiler.profile(df, dataset_name="sample_sales.csv")
        db.drop_all_tables()
        schema = db.load_dataframe(df, "sample_sales")
        st.session_state.table_name = schema.name
        st.session_state.last_result = None
        st.success(f"Loaded sample dataset -- {schema.row_count} rows.")

    if st.session_state.profile is not None:
        st.divider()
        if st.button("\U0001F501 Start over (clear dataset & history)", use_container_width=True):
            db.drop_all_tables()
            for key in ("profile", "table_name", "last_result", "history", "question_input"):
                st.session_state.pop(key, None)
            st.session_state.history = []
            st.rerun()

    if st.session_state.history:
        st.divider()
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

    with st.container(border=True):
        st.markdown("##### \U0001F4CB Dataset preview")
        try:
            preview_df = db.query(f'SELECT * FROM "{st.session_state.table_name}" LIMIT 20', max_rows=20)
            st.dataframe(preview_df, use_container_width=True, height=210)
        except Exception as exc:
            st.caption(f"Preview unavailable: {exc}")

    with st.container(border=True):
        st.markdown("##### \U0001F9EA Data quality summary")
        col1, col2, col3 = st.columns(3)
        col1.metric("Rows", profile.row_count)
        col2.metric("Columns", profile.column_count)
        col3.metric("Duplicate rows", profile.duplicate_row_count)

        with st.expander("Column details", expanded=False):
            st.dataframe(pd.DataFrame([vars(c) for c in profile.columns]), use_container_width=True)

        if profile.warnings:
            with st.expander(f"{len(profile.warnings)} data quality warning(s)", expanded=profile.has_critical_warnings):
                for w in profile.warnings:
                    st.markdown(f"{_badge(w.severity)} **{w.column}** -- {w.message}", unsafe_allow_html=True)
        else:
            st.success("No data quality issues detected.")

    st.markdown("### \U0001F4AC Ask a question")

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

    # Enter-to-submit fix (round 4): a plain st.button() is ONLY triggered by
    # physically clicking it -- pressing Enter while focused on a SEPARATE
    # st.text_input does not set a plain button's return value to True at
    # all (this is standard, well-documented Streamlit behavior, not a
    # guess). st.form()/st.form_submit_button() is specifically Streamlit's
    # mechanism for "pressing Enter in a text input submits the form the
    # same as clicking its submit button" -- wrapping the question input and
    # the Analyze button in a form makes both paths converge on the exact
    # same `ask` boolean and the exact same `current_question` read below,
    # so there is only ONE processing path regardless of how the user
    # triggered it. (Plain st.button widgets, like the example-question
    # buttons above, are NOT allowed inside a form, which is why those stay
    # outside it and use their own on_click callback instead.)
    with st.form(key="question_form", clear_on_submit=False):
        st.text_input(
            "Business question", key="question_input",
            placeholder="What is the total revenue by product category? (press Enter or click Analyze)",
        )
        ask = st.form_submit_button("Analyze", type="primary")

    debug_mode = st.checkbox("Show debug info", value=False, help="Developer diagnostics -- not for normal use.")

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
            # Scope-aware rendering (round 3 fix): every failure used to
            # render as a red st.error() box regardless of WHY it failed --
            # exactly the "red technical failure box for an out-of-scope
            # question" anti-pattern this was built to avoid. Now the
            # visual treatment matches app/agents/scope_classifier.py's
            # AnalysisResult.scope: out_of_scope is a calm informational
            # message, ambiguous is a clarification prompt with clickable
            # options, unsafe is a firm-but-clear refusal, and only a
            # genuine in-scope failure (bad SQL, execution error) uses the
            # red error box.
            if result.scope == "out_of_scope":
                st.info(result.error, icon=SCOPE_ICON["out_of_scope"])
            elif result.scope == "ambiguous":
                st.warning(result.error, icon=SCOPE_ICON["ambiguous"])
                # `clarification_options` (from the scope classifier) are plain
                # numeric column names -- safe to turn into a concrete,
                # always-answerable clicked question. `plan.ambiguous_options`
                # (from a genuinely ambiguous metric choice, e.g.
                # "best-selling") are already full descriptive phrases, not
                # bare column names, so they're shown as text rather than
                # guessed-at as clickable questions.
                if result.clarification_options:
                    st.caption("Choose one, or type your own question above:")
                    opt_cols = st.columns(min(len(result.clarification_options), 4))
                    for i, opt in enumerate(result.clarification_options):
                        with opt_cols[i % len(opt_cols)]:
                            question_for_opt = f"What is the total {opt}?"
                            st.button(opt, key=f"clarify_{opt}", on_click=_use_question, args=(question_for_opt,), use_container_width=True)
                elif result.plan and result.plan.ambiguous_options:
                    st.write("This could mean:")
                    for opt in result.plan.ambiguous_options:
                        st.write(f"- {opt}")
            elif result.scope == "unsafe":
                st.error(result.error, icon=SCOPE_ICON["unsafe"])
            else:
                st.error(f"**Could not answer this question.** {result.error}")
        else:
            with st.container(border=True):
                st.markdown("#### \u2705 Executive summary")
                st.write(result.insight)
                if result.llm_provider == "mock":
                    st.caption("Generated by the deterministic mock LLM (no live model call was made).")

                tab_chart, tab_sql, tab_data, tab_report = st.tabs(["\U0001F4C8 Chart", "\U0001F5C4\ufe0f SQL", "\U0001F522 Data", "\U0001F4E5 Export"])

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
                        import os
                        import tempfile
                        # A unique temp path per call -- a shared hardcoded path
                        # here would let two concurrent sessions race on the
                        # same file (one session's download could get another
                        # session's report). See README "Multi-user and session
                        # isolation" for the same principle applied elsewhere.
                        fd, pdf_path = tempfile.mkstemp(suffix=".pdf", prefix="datapilot_report_")
                        os.close(fd)
                        try:
                            render_pdf_report(result, pdf_path)
                            with open(pdf_path, "rb") as f:
                                st.download_button("Download PDF report", f.read(), file_name="datapilot_report.pdf", mime="application/pdf")
                        finally:
                            os.remove(pdf_path)
                    except Exception as exc:
                        st.caption(f"PDF export unavailable: {exc}")
                    st.markdown(report_md)

            if result.follow_up_questions:
                st.markdown("**Follow-up questions:**")
                for q in result.follow_up_questions:
                    st.button(q, key=f"followup_{q}", on_click=_use_question, args=(q,))
