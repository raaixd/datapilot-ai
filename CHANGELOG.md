# Changelog

## Round 2 -- reliability, schema-awareness, and evaluation depth

### Audit findings (before changing anything)

Inspected every file in `app/`, `tests/`, `eval/`, `frontend/`, and the README. Findings:

1. **Hardcoded `"value"` column name** threaded through `mock_client.py` ->
   `metrics.py` -> `charts.py` -> reports. Any real LLM that named its
   aggregate column something else (e.g. `total_revenue`) would silently
   break metrics computation. This was the single biggest fragility in the
   codebase and is fixed in this round (see below).
2. **Column matching was hint-list-only** (`_METRIC_HINTS` etc.), with no
   concept of synonyms (`item_name` for "product", `sales_amount` for
   "revenue"). Worked only on the bundled sample dataset's exact column
   names -- not demonstrated against a second schema. Fixed with
   `app/data/column_matcher.py` + a second sample dataset
   (`data/sample_ecommerce.csv`) with deliberately different column names,
   now covered by the evaluation suite.
3. **No `LIMIT`/sort-direction extraction** from phrasing like "top five" or
   "in descending order" -- ranking always returned the full grouped result.
   Fixed.
4. **Singular vs. plural ranking not disambiguated**: "which product has the
   highest revenue" and "what are the top products by revenue" were handled
   identically (both returned the full ranked list). Fixed: singular
   phrasing now implies `LIMIT 1`.
5. **No ambiguity detection**: "best-selling product" was silently resolved
   to whichever metric happened to match first. The spec's own example
   (revenue vs. units) is now a real clarification-required case.
6. **Missing-data / duplicate / descriptive-stats questions had no home** --
   they would incorrectly fall through to the metric/aggregation path and
   either produce nonsense SQL or get refused. These are now answered
   directly from the already-computed `DataProfile`, with no SQL involved
   (there's nothing to query -- the profiler already has the answer).
7. **`validate_sql`'s CTE handling was a known, documented gap**
   (`WITH t AS (...) SELECT ... FROM t` was rejected as "unknown table
   t"). Fixed: CTE names are now recognized.
8. **No logging anywhere.** Added a minimal stdlib logging setup
   (`app/core/logging_config.py`) with one call site in the orchestrator
   per analysis, verified with `assertLogs` in tests.
9. **Excel was documented as a project goal but never implemented.**
   `openpyxl` turned out to be genuinely installed in this dev environment
   (unlike fastapi/streamlit/plotly/pydantic, which are still not), so a
   real, tested `.xlsx` loader was added (`app/data/loader.py`) rather than
   just planned.
10. **No `pydantic`, no network to install it.** The tested core still uses
    plain dataclasses with a hand-written `validate()` method that mirrors
    what a pydantic validator would check (allowed enum values for intent /
    aggregation / sort direction, required-field combinations). This is
    documented as a deliberate substitution, not a silent gap -- see
    README "Structured plan validation."

### What changed

- `app/analytics/metrics.py`: metric column is now identified from the
  analysis plan (`metric_alias`, e.g. `total_revenue`, `average_unit_price`,
  `row_count`), never assumed to be a column literally called `value`.
- `app/data/column_matcher.py` (new): synonym-aware concept matching for
  "revenue", "quantity", "product", "date" style concepts.
- `app/data/loader.py` (new): shared CSV/XLSX loading with a clear error if
  a required optional package is missing.
- `app/agents/planner.py`: `AnalysisPlan` gained `limit`, `sort_direction`,
  `time_granularity`, `metric_alias`, `ambiguous_options`, and a `validate()`
  method (`PlanValidationError` on an invalid combination).
- `app/llm/mock_client.py`: rewritten intent classification (12 intents per
  the spec), limit/sort-direction phrase parsing, singular-ranking
  detection, ambiguity detection, synonym-based column resolution.
- `app/agents/orchestrator.py`: three intents (`missing_data`,
  `duplicate_analysis`, `descriptive_stats`) are answered directly from the
  `DataProfile`, without generating or executing SQL.
- `app/agents/sql_validator.py`: recognizes `WITH name AS (...)` CTE names;
  added a few more forbidden tokens (`load_extension`, `readfile`,
  `writefile`).
- `data/sample_ecommerce.csv` (new): second sample dataset with an
  intentionally different schema (`item_name`, `sales_amount`,
  `units_sold`, `order_date`, `sales_channel`) to prove synonym matching
  isn't hardcoded to the first dataset's column names.
- `eval/benchmark.py` / `eval/run_eval.py`: cases now specify which dataset
  they run against; benchmark expanded from 22 to cover both datasets, new
  intents, limits/sort direction, ambiguity, and phrasing variations of the
  same ranking question.
- `tests/`: new test modules for the column matcher, the loader (including
  the Excel path, which -- unlike in round 1 -- is genuinely executable
  here), plan validation, and expanded orchestrator/validator coverage.
- `app/core/logging_config.py` (new).

### What's still NOT verified in this environment

`fastapi`, `streamlit`, `plotly`, `duckdb`, `pydantic`, and `ruff` remain
uninstallable here (no network, no matching local distribution). Everything
touching those stays syntax-checked only, same as round 1 -- see README
"What has and hasn't been executed."

### Additional fixes made while finishing this round

- `app/visualization/charts.py` also hardcoded `"value"` (missed in the
  first pass through this list) -- fixed to read `plan.metric_alias`,
  matching the fix already applied to `metrics.py`.
- Found and fixed a real **operator-precedence bug** in
  `app/data/profiler.py`: `(neg).any() and "price" in col or "amount" in
  col` parsed as `((neg).any() and "price" in col) or ("amount" in col)`,
  so any column merely named `*amount*` was flagged as having negative
  values regardless of whether it actually did. Caught while running the
  CLI demo against the ecommerce dataset, not by inspection -- added a
  regression test (`test_negative_value_warning_only_fires_with_actual_negatives`).
- `app/api/main.py` now uses the shared `app/data/loader.py` (so `/upload`
  genuinely accepts Excel, not just CSV, once FastAPI is installed and
  run), constructs the LLM client eagerly at startup (so a bad
  `LLM_PROVIDER`/missing key fails loudly at boot, not on first request),
  and `/query` now returns 400 with a clear message if no dataset is
  loaded yet instead of a generic error.
- `app/api/schemas.py` gained `AnalysisPlanModel`, a Pydantic mirror of
  `AnalysisPlan` with the same validation rules, for the HTTP boundary.
- `frontend/streamlit_app.py` rewritten for the UI checklist in the spec:
  example questions, dataset preview, CSV download of results, PDF/Markdown
  report download, empty states, ambiguity clarification display, and a
  visible "mock LLM, no live model call" notice when `LLM_PROVIDER=mock`.
- `scripts/demo_cli.py` switched to the shared loader (`--file` now accepts
  `.xlsx`/`.xls` too, not just `.csv`).
- Added `scripts/run_all.sh` / `scripts/run_all.ps1` for optional
  single-command startup, without removing the documented two-terminal
  workflow.
- README rewritten from scratch per the full spec checklist, including
  Windows PowerShell install/run commands and an explicit
  implemented-vs-written-but-unverified table.
