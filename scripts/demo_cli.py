"""
Command-line demo of the full pipeline, requiring only the core
dependencies (pandas, numpy, reportlab) -- no FastAPI/Streamlit/plotly
needed. Useful for a quick interview demo or for verifying a fresh clone
works before installing the web-layer dependencies. Supports .csv, .xlsx,
and .xls via the same app/data/loader.py used by the API and Streamlit app.

Usage:
    PYTHONPATH=. python3 scripts/demo_cli.py "What is the total revenue by region?"
    PYTHONPATH=. python3 scripts/demo_cli.py --file path/to/other.xlsx "How many orders are there?"
"""

from __future__ import annotations

import argparse
import json
import sys

from app.agents.orchestrator import Orchestrator
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.data.database import AnalyticalDatabase
from app.data.loader import load_tabular_file
from app.data.profiler import DataProfiler
from app.llm.factory import build_llm_client
from app.reports.markdown_report import render_markdown_report


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="DataPilot AI command-line demo")
    parser.add_argument("question", help="Business question to ask")
    parser.add_argument(
        "--file",
        "--csv",
        dest="file",
        default="data/sample_sales.csv",
        help="Path to a .csv, .xlsx, or .xls file (default: bundled sample dataset)",
    )
    parser.add_argument("--report", metavar="PATH", help="Write a Markdown report to this path")
    args = parser.parse_args()

    try:
        df = load_tabular_file(args.file, args.file)
    except Exception as exc:
        print(f"Could not load '{args.file}': {exc}")
        return 1

    profiler = DataProfiler()
    profile = profiler.profile(df, dataset_name=args.file)

    print(f"Loaded {profile.row_count} rows, {profile.column_count} columns from {args.file}")
    if profile.warnings:
        print(f"{len(profile.warnings)} data quality warning(s):")
        for w in profile.warnings:
            print(f"  [{w.severity.upper()}] {w.message}")
    print()

    settings = get_settings()
    db = AnalyticalDatabase(backend=settings.database_backend, path=":memory:")
    table_name = args.file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    db.load_dataframe(df, table_name)

    orchestrator = Orchestrator(
        db, build_llm_client(settings), max_result_rows=settings.max_result_rows, settings=settings
    )
    result = orchestrator.analyze(args.question, data_profile=profile)

    print(f"Q: {result.question}")
    if not result.success:
        print(f"-> Could not answer: {result.error}")
        return 1

    print(f"SQL: {result.sql}")
    print(f"Insight: {result.insight}")
    print(f"Metrics: {json.dumps(result.metrics, indent=2)}")
    if result.follow_up_questions:
        print("Follow-up questions:")
        for q in result.follow_up_questions:
            print(f"  - {q}")

    if args.report:
        with open(args.report, "w") as f:
            f.write(render_markdown_report(result))
        print(f"\nReport written to {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
