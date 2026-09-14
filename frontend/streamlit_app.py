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
from app.reports.pdf_report import render_pdf_report_bytes
from app.visualization.charts import build_chart

configure_logging()
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
def _get_backend():
    settings = get_settings()
    db = AnalyticalDatabase(backend=settings.database_backend, path=":memory:")
    try:
        llm = build_llm_client(settings)
    except Exception as exc:
        st.error(
            f"Could not initialize the LLM client for LLM_PROVIDER={settings.llm_provider}: {exc}\n\n"
            "Check your .env file against .env.example, or set LLM_PROVIDER=mock to run without any API key."
        )
        st.stop()
    orchestrator = Orchestrator(db, llm, max_result_rows=settings.max_result_rows, settings=settings)
    return db, orchestrator, DataProfiler(), settings


db, orchestrator, profiler, settings = _get_backend()

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
    with st.expander("Example questions"):
        for q in EXAMPLE_QUESTIONS:
            if st.button(q, key=f"example_{q}"):
                st.session_state["_pending_question"] = q

    question = st.text_input(
        "Business question",
        value=st.session_state.pop("_pending_question", ""),
        placeholder="What is the total revenue by product category?",
    )
    ask = st.button("Analyze", type="primary")

    if ask and not question.strip():
        st.warning("Type a question first, or click one of the example questions above.")

    if ask and question.strip():
        with st.spinner("Analyzing..."):
            result = orchestrator.analyze(question, data_profile=profile)
        st.session_state.history.append(question)
        st.session_state.last_result = result

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
                    st.download_button(
                        "Download PDF report", render_pdf_report_bytes(result),
                        file_name="datapilot_report.pdf", mime="application/pdf",
                    )
                except Exception as exc:
                    st.caption(f"PDF export unavailable: {exc}")
                st.markdown(report_md)

            if result.follow_up_questions:
                st.markdown("**Follow-up questions:**")
                for q in result.follow_up_questions:
                    if st.button(q, key=f"followup_{q}"):
                        st.session_state["_pending_question"] = q
                        st.rerun()
