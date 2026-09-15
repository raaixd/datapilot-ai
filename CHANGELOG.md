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

## Round 3 -- scope classification, DB/session reliability, NLU fixes, RAG

### The reported bug and its fix

An unrelated question ("meaning of life") produced a misleading
column-mapping error instead of an honest "this isn't about your data"
response. Root cause: there was no scope-classification stage at all --
every kind of failure (off-topic, vague, or a genuinely missing column)
collapsed into one message from the planner. Fixed with a new,
deterministic (no LLM call) `app/agents/scope_classifier.py` that runs
before planning and classifies every question into `in_scope | ambiguous |
out_of_scope | unsafe`, using schema-overlap as the primary signal (not a
hardcoded topic denylist -- generalizes to any dataset). All 20 of the
bug report's own example questions, across all four categories, are now
individually unit-tested and pass. See the message exchange in this
project's history for the full root-cause writeup and the phased plan that
preceded implementation.

### Database / thread / session reliability

Reproduced the exact reported `SQLite objects created in a thread can only
be used in that same thread` error first (see
`tests/test_database_thread_safety.py::test_default_memory_mode_is_not_thread_safe`),
then fixed it. Chose a file-backed database with a fresh, short-lived
connection per operation over three rejected alternatives (`check_same_thread=False`
alone, a fresh connection against `:memory:`, a connection pool) -- full
tradeoff writeup in `app/data/database.py`'s module docstring.
`AnalyticalDatabase.create_session_database()` is the new entry point for
Streamlit/API use; the plain `:memory:` constructor remains for
tests/eval/CLI (single-threaded, no temp files). Proven with real
`threading.Thread` tests: load-on-one-thread/query-on-another, 20
concurrent readers, and the original failure reproduced then fixed.

Also fixed a **cross-session data leakage bug** found while wiring this
in: `frontend/streamlit_app.py`'s `@st.cache_resource` on the
db/orchestrator meant every Streamlit user on the same server shared one
dataset. Fixed by moving the per-session database into
`st.session_state` (genuinely session-isolated) and keeping only the
stateless LLM client/settings in `st.cache_resource`.

The same leakage existed in `app/api/main.py` (one global dataset shared
by every API client) -- fixed with a new, deliberately FastAPI-independent
`app/api/session_manager.py` (so it's actually unit-testable here: 12
tests, including the exact "client A's data doesn't leak into client B's
session" scenario, proven directly). Each session gets its own
file-backed `AnalyticalDatabase`, a server-generated UUID, and a
simple TTL-based expiry (no background scheduler needed at this scale --
tradeoff documented in that module's docstring). `POST /upload` now
returns a `session_id`; `POST /query` requires it. Added `MAX_UPLOAD_MB`
and `SESSION_TTL_MINUTES` settings, and a `DELETE /session/{id}` endpoint.

### NLU / query-quality fixes (found via direct testing, not just the literal spec example)

The literal "Which products experienced declining sales?" already worked
correctly (routes to a `trend_by_dimension` intent: `GROUP BY dimension,
period`, with per-entity increase/decrease detection done in
`app/analytics/metrics.py` over the clean query result -- not in SQL).
But natural rewordings failed:

- "Which product categories are declining?" / "Show me revenue by
  categories" -- failed because `product_category` (singular) didn't
  match `categories` (plural) as a substring. Fixed by adding conservative
  English singularization to the shared `app/data/column_matcher.py::normalize()`,
  used consistently everywhere column/concept names are compared against
  question text.
- "Show me revenue by categories" also failed because the group-by
  detector required the EXACT full column name immediately after "by"
  (`\bby\s+product category\b`) -- loosened to "a dimension was already
  resolved AND 'by' appears anywhere in the question", which is
  significantly more robust to real phrasing.
- "What's declining in sales?" (no dimension named) fell back to one flat
  total, which doesn't show a decline at all -- now falls back to the
  plain `trend` intent (revenue over time) instead, so there's at least a
  visible pattern to look at.
- The fallback clarification message ("Could not map this question to a
  specific measurable column...") was reworded to be conversational
  rather than sounding like an internal error, independent of the
  scope-classifier's own (separate) friendly messages.

### RAG / retrieval layer (new -- did not exist before this round)

Added `app/rag/`: a small, hand-curated business glossary and validated
question-pattern library (`knowledge_base.py`), retrieved via schema-aware
keyword/concept matching (`retriever.py`) -- deliberately NOT a vector
store, which isn't justified infrastructure for a knowledge base this
small (tradeoff documented in the module docstring; revisit if the
glossary/example library grows much larger). This is wired into BOTH
`app/agents/planner.py` and `app/agents/sql_generator.py`'s actual prompts
sent to the LLM -- proven with a recording-client test harness
(`tests/test_rag_prompt_integration.py`) that inspects the literal prompt
text, not just that the retriever module runs. Found and fixed a real
prompt-corruption risk while wiring this in: inserting the CONTEXT block
between the mock client's `PLAN:` and `SCHEMA:` markers broke its
non-greedy regex parsing -- fixed by placing CONTEXT before PLAN
consistently (documented in `app/llm/prompts.py`).

### Frontend bug: "Type a question first..." warning firing on valid input

Traced per the reported checklist. `streamlit` is not installed in this
project's dev sandbox (no network access), so this could NOT be confirmed
against a live run -- stated plainly rather than claimed fixed-and-verified.
Found a specific, well-documented Streamlit anti-pattern in the code:
`st.text_input(..., value=st.session_state.pop(...))` with no explicit
`key=`. Replaced with the documented-safe pattern: a stable `key`, and
`on_click` callbacks (which run and complete before the script body
reruns) for every "fill this question in" action (example buttons,
follow-up buttons), removing the entire class of value/key race this
anti-pattern is known to cause, regardless of the exact mechanism behind
the originally reported symptom. Added a "Show debug info" toggle
(session-state snapshot) for live diagnosis if the symptom recurs.

### Still not executed in this environment

`fastapi`, `streamlit`, `plotly`, `duckdb`, `pydantic`, `ruff` remain
uninstallable here. `openpyxl` remains genuinely installed and tested.

## Round 3, continued -- UI polish + two more real bugs found while doing it

### Scope-aware result rendering (the actual UI fix, not just cosmetics)

`frontend/streamlit_app.py` previously rendered EVERY failed result as a
red `st.error()` box, regardless of why it failed -- which was still the
"red technical failure box for an out-of-scope question" anti-pattern,
just one layer further out than the original bug report (the message text
was already fixed in the scope-classifier work above; the *rendering*
hadn't caught up). Now `AnalysisResult.scope` drives the visual treatment:
`out_of_scope` -> calm `st.info`, `ambiguous` -> `st.warning` with
clickable clarification buttons (built from `clarification_options`, which
are always plain numeric column names, so "What is the total {option}?"
is always a coherent, answerable question -- kept as text-only for
`plan.ambiguous_options`, which are already full descriptive phrases, not
bare column names, so guessing a fill-in question from them would be
fragile), `unsafe` -> a firm `st.error`, and only a genuine in-scope
failure (bad SQL, execution error) uses the plain red box.

### Two more real bugs found while making this change

1. **Stale-table accumulation**: `AnalyticalDatabase` never dropped a
   previous table when a session loaded a second dataset under a
   different filename -- both tables ended up coexisting, and the
   planner's `next(iter(schema))` would pick whichever one came first in
   dict order, not necessarily the one the user just uploaded. Added
   `AnalyticalDatabase.drop_all_tables()` and call it before every load in
   Streamlit's uploader/sample-dataset buttons, the new "Start over"
   button, and FastAPI's `/upload`. `tests/test_database_thread_safety.py`
   has both a test documenting the bug (two tables coexisting) and one
   proving the fix (exactly one table remains after `drop_all_tables()`).
2. **Shared hardcoded PDF path**: the PDF-report download button wrote to
   a fixed path (`/tmp/_datapilot_report.pdf`) -- two concurrent Streamlit
   sessions generating a report at the same time could race and one could
   download the other's file. Fixed with `tempfile.mkstemp()` (a unique
   path per call, removed after use) -- the same "no shared mutable state
   across sessions" principle applied everywhere else in this round.

### Other polish

- Custom CSS (card-style metrics, consistent spacing/typography, styled
  tabs) -- written against Streamlit's documented CSS hooks, **not
  visually verified** (no live `streamlit run` here); said so directly in
  the code comment rather than implied it was checked.
- Sidebar now shows the currently-loaded dataset name and a "Start over"
  button (clears dataset + history + question input in one action).
- Removed genuinely dead code (`pdf_buffer = io.BytesIO()` was assigned
  and never used).

## Round 3, continued -- anomaly detection (implemented, not just declared)

`"anomaly_detection"` had been listed in `app/agents/planner.py`'s
`VALID_INTENTS` since round 2 but nothing ever actually produced or
handled that intent -- a real, if quiet, gap between what the type system
allowed and what the app did. Implemented now:

- `app/llm/mock_client.py` recognizes anomaly/outlier phrasing and
  resolves a target metric column the same way other intents do.
- `app/agents/orchestrator.py` handles `anomaly_detection` as a special
  case that does NOT go through the normal LLM-driven SQL-generation path:
  SQLite has no built-in `STDDEV` (and `SQRT` isn't guaranteed available),
  so asking the LLM to write a self-contained outlier-detection query
  would mean either it fails outright or it has to invent statistics.
  Instead, the mean and standard deviation come from
  `app/data/profiler.py`'s already-computed, already-tested column
  statistics (real pandas math, computed once at profiling time) and are
  substituted into a simple `WHERE ABS(col - mean) > 2*std` query as
  literal numbers -- which still goes through the normal `validate_sql()`
  safety check before running, same as every other query in this app.
- Handles the zero-variance edge case explicitly (a column where every
  value is identical) rather than risking a divide-by-zero or a
  meaningless "everything is an anomaly" result.
- Verified directly against a dataset with a deliberately planted outlier
  (correctly found, exactly 1 row), a uniform dataset (correctly finds
  none), a zero-variance dataset (correctly explains why detection isn't
  meaningful), and the real bundled `sample_sales.csv` (finds 29 outlier
  rows in `revenue` out of 506 -- a real number from a real query, not
  asserted in advance). 6 new orchestrator-level tests, 2 new eval cases
  (65/65 passing total).
