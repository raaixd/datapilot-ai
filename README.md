# DataPilot AI -- Intelligent Business Analytics

Ask a business question in plain English about a CSV or Excel file you
upload, and get back the SQL that answers it, computed metrics, a chart,
plain-language insight, and an exportable report -- with a deterministic
safety layer that never executes anything but a validated, read-only
`SELECT`, and that asks for clarification instead of guessing when a
question is genuinely ambiguous.

> (MIT License). See [`NOTICE.md`](NOTICE.md) and

## What problem this solves

A basic "upload a file and chat with an AI about it" workflow has two
recurring failure modes: it either (a) lets the model write and run
arbitrary SQL against your data with no safety net, or (b) has the model
eyeball a sample of the data and free-associate an answer with no way to
check where the numbers came from. DataPilot AI is built around closing
both gaps:

- **Every numeric answer is traceable to an executed, validated SQL query**
  you can inspect -- never a number the model "read off" the data.
- **SQL safety is enforced in code** (`app/agents/sql_validator.py`), not
  by asking the model nicely. Malicious or destructive text has nowhere to
  go: the SQL that actually runs is generated from a small structured plan,
  templated, and then independently validated before execution.
- **Genuinely ambiguous questions get a clarification request, not a
  silent guess.** "What's the best-selling product?" could mean highest
  revenue or highest unit sales -- the app says so instead of picking one.

## Table of contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Running it](#running-it)
- [How the API and frontend relate](#how-the-api-and-frontend-relate)
- [Sample questions](#sample-questions)
- [Testing](#testing)
- [Evaluation](#evaluation)
- [What has and hasn't been executed](#what-has-and-hasnt-been-executed-in-development)
- [Structured plan validation](#structured-plan-validation)
- [Security considerations](#security-considerations)
- [Known limitations](#known-limitations)
- [Future improvements](#future-improvements)
- [How this differs from the reference project](#how-this-differs-from-the-reference-project)
- [Credits](#credits)

## Features

**Implemented and verified** (see [Testing](#testing) / [Evaluation](#evaluation) for how):

- CSV **and Excel (.xlsx)** upload -- no fixed pre-seeded schema.
- Data profiling before analysis: row/column counts, per-column types,
  missing values, duplicate rows, invalid numeric strings, unparseable
  dates, high-cardinality categorical columns.
- Structured, **validated** analysis plan (intent, grouping column, metric
  column, aggregation, filters, sort direction, limit, date column, time
  granularity, chart type, clarification flag) generated before any SQL --
  see [Structured plan validation](#structured-plan-validation).
- **Synonym-aware schema matching**: recognizes `item_name`/`product_name`
  as "product", `sales_amount`/`total_sales` as "revenue",
  `transaction_date`/`purchase_date` as "date", etc. -- not hardcoded to
  one dataset's exact column names (`app/data/column_matcher.py`).
- 12 classified intents: aggregation, ranking (with limit + sort
  direction), grouped comparison, time-series trend, percentage change,
  descriptive statistics, missing-data analysis, duplicate analysis,
  trend-by-dimension (e.g. "which products are declining"), and
  unsupported/ambiguous handling.
- **Ambiguity detection**: a question with more than one plausible metric
  interpretation (e.g. "best-selling") triggers a clarification request
  instead of a silent guess.
- SQL safety enforced in code, independent of the LLM's behavior --
  see [Security considerations](#security-considerations).
- Explainable results: every answer bundles the SQL used, computed
  metrics (named after the actual metric, e.g. `total_revenue` -- never a
  hardcoded generic column), an insight grounded only in those metrics,
  data quality warnings, and follow-up questions.
- Markdown and PDF report export.
- Provider-agnostic LLM layer: deterministic offline mock (default, used
  by tests) / Anthropic API / local Ollama model, swapped with one
  environment variable.
- A real evaluation suite across **two datasets with different schemas**:
  40 cases (business questions, phrasing variations of the same question,
  safety probes, consistency checks), run against the actual orchestrator
  and reported with an honest pass rate -- see [Evaluation](#evaluation).

**Written but not executable in this project's dev sandbox** (no network
access to install `fastapi`/`streamlit`/`plotly`/`duckdb`/`pydantic`/`ruff`
there -- see [What has and hasn't been executed](#what-has-and-hasnt-been-executed-in-development)):
the FastAPI backend, the Streamlit UI, Plotly charts, live Anthropic API
calls, the DuckDB backend option, and pydantic-based HTTP validation. These
are complete, syntax-checked code, calling into a core that IS tested --
but verify them yourself with `pip install -r requirements.txt` before
presenting or relying on them.

## Architecture

```
                    ┌─────────────────────────┐
                    │   Streamlit frontend      │   (in-process; talks
                    │  upload · ask · charts    │    directly to the
                    └────────────┬─────────────┘    orchestrator below --
                                 │                    does NOT call the
                                 │                    FastAPI backend)
                    ┌────────────▼─────────────┐
                    │       Orchestrator          │◄──── FastAPI backend
                    │ (app/agents/orchestrator)   │      (/upload, /query,
                    └───┬───────┬───────┬───────┘       /health) talks to
       ┌────────────────┘       │       └───────────┐   the SAME orchestrator
       ▼                        ▼                       ▼               class, over HTTP
┌─────────────────┐   ┌────────────────────┐  ┌────────────────────┐
│ AnalysisPlanner  │   │   SQLGenerator      │  │  SQL Validator      │
│ question+schema  │──►│  plan+schema→SQL     │─►│ deterministic,      │
│ → validated plan │   │                     │  │ schema/CTE-aware     │
└────────┬─────────┘   └─────────────────────┘  └──────────┬──────────┘
        │ (LLMClient: mock | anthropic | ollama)            │ safe SQL
        ▼                                                    ▼
┌─────────────────┐                               ┌─────────────────────┐
│  Data Profiler   │                               │ AnalyticalDatabase    │
│ (quality checks) │                               │ (SQLite, or DuckDB)   │
└─────────────────┘                               └──────────┬──────────┘
                                                                │ result rows
                                                                ▼
                                                ┌───────────────────────────┐
                                                │  Metrics (plan-driven      │
                                                │  metric_alias) + Insight   │
                                                │  + Chart + Report          │
                                                └───────────────────────────┘
```

Some intents (`missing_data`, `duplicate_analysis`, `descriptive_stats`)
skip the SQL path entirely -- the orchestrator answers them straight from
the already-computed data profile, since there's nothing to query for
those.

## Tech stack

| Layer | Technology | Status here |
|---|---|---|
| Frontend | Streamlit | written, untested (no network to install) |
| Backend API | FastAPI + Pydantic | written, untested (no network to install) |
| Analytical database | SQLite (default) or DuckDB (optional) | SQLite tested; DuckDB untested |
| Data loading | Pandas + openpyxl (Excel) | **tested** -- openpyxl is genuinely installed here |
| Data processing | Pandas + NumPy | tested |
| Visualization | Plotly | written, untested |
| Reports | Markdown + ReportLab (PDF) | tested |
| LLM | mock (tested) / Anthropic API (untested) / Ollama (untested) | see above |
| Plan validation | stdlib dataclass + hand-written validator (pydantic mirror at the API boundary) | tested (core); untested (API mirror) |
| Testing | stdlib `unittest` (pytest-compatible) | tested |
| Lint | Ruff (configured, not runnable here -- no network) | not verified |
| Containers | Docker + Docker Compose | written, untested |
| CI | GitHub Actions | written, untested |

## Project structure

```
datapilot-ai/
├── app/
│   ├── core/
│   │   ├── config.py              # environment-driven settings (dataclass)
│   │   └── logging_config.py      # centralized stdlib logging setup
│   ├── data/
│   │   ├── database.py            # SQLite/DuckDB adapter: load, describe schema, query
│   │   ├── loader.py              # shared CSV/XLSX/XLS loading (tested incl. Excel)
│   │   ├── column_matcher.py      # synonym-aware concept matching, limit/sort parsing
│   │   └── profiler.py            # data-quality profiling
│   ├── llm/
│   │   ├── base.py                # LLMClient interface
│   │   ├── mock_client.py         # deterministic offline client used by tests + eval
│   │   ├── anthropic_client.py    # real Anthropic + Ollama clients
│   │   ├── factory.py             # picks a client based on LLM_PROVIDER
│   │   └── prompts.py             # prompt templates
│   ├── agents/
│   │   ├── planner.py             # question+schema -> validated AnalysisPlan
│   │   ├── sql_generator.py       # AnalysisPlan -> SQL text (via LLM)
│   │   ├── sql_validator.py       # deterministic, CTE-aware SQL safety validation
│   │   └── orchestrator.py        # wires the whole pipeline together
│   ├── analytics/metrics.py       # plan-driven metric computation (no hardcoded column names)
│   ├── visualization/charts.py    # Plotly chart construction
│   ├── reports/{markdown,pdf}_report.py
│   └── api/
│       ├── main.py                # FastAPI app: /upload, /query, /health
│       └── schemas.py             # Pydantic request/response + plan mirror
├── frontend/streamlit_app.py      # Streamlit dashboard
├── data/
│   ├── sample_sales.csv           # bundled sample #1 (region/product_category/revenue schema)
│   └── sample_ecommerce.csv       # bundled sample #2 -- deliberately different column names
├── tests/                         # 99 unit/integration tests
├── eval/
│   ├── benchmark.py                # 40 cases across both datasets
│   └── run_eval.py                 # runs the benchmark, prints an honest pass rate
├── scripts/
│   ├── demo_cli.py                # dependency-light CLI demo (pandas/numpy/reportlab/openpyxl)
│   ├── run_all.sh / run_all.ps1   # optional single-command dev launcher
├── Dockerfile / docker-compose.yml
├── .github/workflows/ci.yml
└── CHANGELOG.md                   # what changed and why, across improvement rounds
```

## Installation

**macOS / Linux:**
```bash
git clone <this-repo-url> datapilot-ai
cd datapilot-ai
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

**Windows (PowerShell):**
```powershell
git clone <this-repo-url> datapilot-ai
cd datapilot-ai
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks the activation script, run once (as the current user):
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Environment variables

All variables have safe defaults (see `app/core/config.py`) -- the app runs
out of the box with **zero API keys** using `LLM_PROVIDER=mock`.

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock` \| `anthropic` \| `ollama` \| `freellmapi` |
| `ANTHROPIC_API_KEY` | (empty) | required only if `LLM_PROVIDER=anthropic`; never hardcode this -- it's read from the environment. Missing it raises a clear `ValueError` at startup, not a silent failure. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | used only if `LLM_PROVIDER=ollama` |
| `LLM_MODEL` | `claude-sonnet-4-6` | model name passed to whichever provider is selected |
| `DATABASE_BACKEND` | `sqlite` | `sqlite` (tested) \| `duckdb` (untested here, requires `pip install duckdb`) |
| `DATABASE_PATH` | `data/datapilot.db` | ignored for the in-memory API/CLI/Streamlit paths, which always use `:memory:` |
| `MAX_RESULT_ROWS` | `1000` | hard cap enforced by the SQL validator, not just a suggestion |
| `QUERY_TIMEOUT_SECONDS` | `10` | currently informational (SQLite queries here are not long-running); wire into a real timeout if you swap in a slower backend |
| `LOG_LEVEL` | `INFO` | passed to `app/core/logging_config.py` |

## Running it

**Backend API** (default port **8000**):
```bash
uvicorn app.api.main:app --reload
# -> http://localhost:8000/docs
```

**Frontend** (default port **8501**, in a second terminal):
```bash
streamlit run frontend/streamlit_app.py
# -> http://localhost:8501
```

**Both with one command** (starts the API in the background, then the
frontend in the foreground; Ctrl+C stops both):
```bash
bash scripts/run_all.sh          # macOS/Linux
powershell scripts/run_all.ps1   # Windows
```

**Docker (both services):**
```bash
docker-compose up --build
```

**No-install CLI demo** (only needs pandas/numpy/reportlab/openpyxl --
useful for a quick sanity check or an interview walkthrough; supports
`.csv`, `.xlsx`, and `.xls`):
```bash
PYTHONPATH=. python3 scripts/demo_cli.py "What is the total revenue by region?"
PYTHONPATH=. python3 scripts/demo_cli.py --file data/sample_ecommerce.csv "Rank items by sales amount"
```

## How the API and frontend relate

- **They are independent processes that do NOT need each other.** The
  Streamlit app imports and calls the orchestrator directly, in-process --
  it does not make HTTP requests to the FastAPI backend. You can run just
  `streamlit run frontend/streamlit_app.py` with no backend running at all.
- The FastAPI backend exists as a separate, stateless-per-process HTTP
  interface to the *same* core, for programmatic/API consumers.
- **If the backend is unavailable**, the Streamlit app is unaffected (see
  above). If you're calling the API directly and it's down, requests will
  simply fail to connect -- there is currently no separate "backend
  unavailable" UI state in Streamlit, because it never depends on the
  backend being up. This is a known simplification if you later change the
  frontend to call the API over HTTP instead of in-process.
- **How uploaded files are handled**: the API's `/upload` endpoint holds
  exactly one active dataset at a time, in memory (`AnalyticalDatabase`
  backed by SQLite `:memory:`). Uploading a new file replaces the current
  table entirely -- there is no multi-dataset session state. The Streamlit
  app behaves the same way per browser session (`st.cache_resource`).
- **Resetting between datasets**: just upload again (API) or click "Use
  sample sales dataset" / upload a new file again (Streamlit) -- the new
  file's table replaces the old one in the same in-memory database.
- **Health check**: `GET /health` reports the configured LLM provider,
  database backend, and whether a dataset is currently loaded. Check this
  first if a client can't get a sensible response from `/query`.

## Sample questions

Against `data/sample_sales.csv` (columns: `order_date`, `region`,
`product_category`, `customer_id`, `quantity`, `unit_price`, `revenue`):

- "What is the total revenue?"
- "What are the top 5 regions by revenue?"
- "Which region has the highest revenue?"
- "Show me the monthly revenue trend"
- "Which product categories experienced declining sales?"
- "Compare revenue between regions"
- "How much data is missing?"
- "Are there any duplicate rows?"

Against `data/sample_ecommerce.csv` (deliberately different column names --
`transaction_date`, `sales_channel`, `item_name`, `units_sold`,
`sales_amount` -- to demonstrate synonym-based matching):

- "What is the total sales amount?"
- "Rank items by sales amount"
- "Which item sold the most units?"

Questions expected to be **refused rather than guessed at** (see
[Known limitations](#known-limitations)):

- "What is the total profit margin?" (no such column exists)
- "What is the best-selling product category?" (ambiguous: revenue or
  units? -- the app asks)
- "What is the meaning of life?" (unrelated to the dataset)

## Testing

```bash
pytest tests/ -v
```

As of this writing: **99 unit/integration tests, all passing.** Coverage
includes the profiler, the SQL validator (including CTE handling and
adversarial/injection cases), the column matcher, the CSV/Excel loader
(the Excel path is genuinely executed, not just written -- `openpyxl` is
installed in this dev environment), plan validation, metrics computation,
report generation, and end-to-end orchestrator integration tests (ranking
with limits, sort direction, ambiguity, the three profile-only intents,
declining-sales-by-dimension, an alternate-schema dataset, and a
logging-output check).

## Evaluation

```bash
PYTHONPATH=. python3 eval/run_eval.py
```

As of this writing: **40/40 cases pass (100%)** across `data/sample_sales.csv`
and `data/sample_ecommerce.csv` combined -- aggregation, ranking (including
four phrasing variations of the same underlying ranking question), grouped
comparison, trend, percentage change, declining-sales-by-dimension, the
three profile-only intents, ambiguous questions, missing-column questions,
off-topic questions, a prompt-injection probe, a simulated malicious-LLM
probe, a consistency check, and an empty-dataset check.

**This 100% figure is a real, reproducible result of running the command
above against this exact code** -- it is not asserted in advance; the
script computes and prints it, and lists every individual case (with its
category) and, on any failure, exactly what didn't match. Two real bugs
were found and fixed via this exact process during round-2 development
(documented in `CHANGELOG.md`) -- the number was not always 100%, and this
README does not claim it is guaranteed to stay 100% as the codebase
changes; re-run it yourself to check.

## What has and hasn't been executed in development

This project was built and tested in a sandboxed environment with **no
network access**. `pandas`, `numpy`, `reportlab`, and (unexpectedly, but
genuinely) `openpyxl` were pre-installed there; `fastapi`, `streamlit`,
`plotly`, `duckdb`, `pydantic`, and `ruff` were not, and could not be
installed (no matching local package index). In the interest of never
claiming untested code works:

| Area | Status |
|---|---|
| `app/core`, `app/data` (including the Excel loader), `app/agents`, `app/analytics`, `app/llm/mock_client.py`, `app/reports/*` | **Executed and tested** (99 unit/integration tests) and exercised end-to-end via `scripts/demo_cli.py` and the evaluation suite, against both bundled datasets. |
| `app/agents/sql_validator.py` | **Executed and tested directly**, including CTE handling and adversarial/injection cases. |
| `app/llm/anthropic_client.py` | Written to the real Anthropic SDK, syntax-checked, **not run against a live API** here. Test locally with a real key first. |
| `app/visualization/charts.py` (Plotly) | Written and syntax-checked; the *data shape* it consumes (plan-driven `metric_alias`) IS verified via the tested core, but Plotly rendering itself is **not executed** here (plotly not installable offline). |
| `app/api/main.py` / `app/api/schemas.py` (FastAPI + Pydantic) | Written and syntax-checked, **not started/hit with a real request** here. The orchestrator logic it calls into IS tested. The pydantic `AnalysisPlanModel` mirror is untested for the same reason. |
| `frontend/streamlit_app.py` | Written and syntax-checked, **not run with `streamlit run`** here. |
| `Dockerfile` / `docker-compose.yml` / CI workflow / `scripts/run_all.*` | Written to standard patterns, **not built/run** here (no Docker/network access). |
| `DATABASE_BACKEND=duckdb` | Written, **not exercised** here (`duckdb` not installed). `sqlite` (the default) is fully tested. |

**Before presenting or relying on this project, install the full
`requirements.txt` locally (with network access) and actually run**
`uvicorn app.api.main:app`, `streamlit run frontend/streamlit_app.py`, and
`docker-compose up --build`, confirming each behaves as documented.
Files in the "not executed" rows include an inline `NOTE ON TESTING`
comment saying the same thing.

## Structured plan validation

The spec for this project called for Pydantic (or an equivalent) to
validate the LLM's structured output. `pydantic` could not be installed in
this dev environment (no network access to PyPI or a local mirror). Rather
than silently skip validation, the tested core (`app/agents/planner.py`)
implements an equivalent contract by hand:

- `AnalysisPlan.validate()` checks that `intent` is one of a fixed set,
  `aggregation` is one of `sum`/`avg`/`min`/`max`/`count`/`None`,
  `sort_direction` is `asc`/`desc`/`None`, `chart_type` is a known type,
  `limit` (if set) is a positive integer, and that an answerable,
  non-profile-only plan actually names a metric column.
- Any plan failing this check is discarded and the question is answered
  with `intent="unsupported"` and a clear `clarification_needed` message --
  never silently coerced into "something plausible."
- This runs automatically inside `AnalysisPlanner.plan()`, so it's already
  exercised on every orchestrator test in `tests/test_orchestrator.py`,
  plus its own dedicated tests in `tests/test_plan_validation.py`.

Separately, `app/api/schemas.py` defines `AnalysisPlanModel`, a
field-for-field Pydantic mirror of `AnalysisPlan` with the same rules
expressed as `field_validator`s, for the HTTP boundary specifically. This
is a second, independent validation pass for API consumers -- not a
replacement for the one above, and (like the rest of the API layer)
untested here since `pydantic` isn't installed.

## Security considerations

`app/agents/sql_validator.py` enforces, in code (never relying on the
LLM's prompt-following behavior):

- Only a single `SELECT` or `WITH ... SELECT` statement is accepted.
- Multiple statements (semicolon-separated) are rejected.
- A blocklist of DDL/DML/extension keywords (`INSERT`, `UPDATE`, `DELETE`,
  `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `PRAGMA`, `ATTACH`,
  `LOAD_EXTENSION`, `READFILE`, `WRITEFILE`, ...) is matched as whole
  tokens.
- References to system/catalog tables (`sqlite_master`,
  `information_schema`, ...) are rejected.
- `UNION`-based statements are rejected.
- **CTE names are recognized** (`WITH x AS (...) SELECT ... FROM x`), while
  the base tables used *inside* a CTE body are still validated against the
  real schema -- a CTE cannot be used to smuggle in an unvalidated table
  reference.
- Every non-CTE referenced table must exist in the actual loaded schema.
- A `LIMIT` clause is enforced (added automatically if missing, capped if
  excessive) -- `MAX_RESULT_ROWS` is a hard ceiling, not a suggestion.
- The database connection used for querying never executes anything the
  validator hasn't approved -- there is no code path from raw LLM text to
  `cursor.execute`.
- **This is a regex/tokenizer-based validator, not a full SQL parser** (no
  `sqlparse`/`sqlglot` was installable offline here -- see the module
  docstring in `sql_validator.py` for the specific heuristics and their
  known edges, e.g. deeply nested or exotic CTE syntax).

This is demonstrated, not just claimed: `tests/test_sql_validator.py` and
`eval/run_eval.py` both include a simulated "malicious LLM" that returns
`DROP TABLE ...` / `DELETE FROM ...`, and a prompt-injection question, and
assert the table still exists and no destructive keyword ever appears in
the executed SQL afterward.

## Known limitations

Being direct about what this project does *not* do, since overclaiming is
worse than an honest scope statement:

- **No arbitrary `WHERE`-clause filters beyond simple equality on a known
  categorical value** (e.g. "...for the North region" works because
  "North" is a real, sampled value of the `region` column; a numeric range
  filter like "...over $500" is not currently supported).
- **The mock LLM is a rule-based stand-in, not a language model.** It uses
  keyword/phrase matching and synonym lookup, not semantic understanding --
  it can misparse phrasing well outside the patterns it was written for.
  It exists specifically so tests and the evaluation suite are
  deterministic and don't require an API key or network access; when
  `LLM_PROVIDER=mock`, the app never claims a live model was used (see
  `AnalysisResult.llm_provider`, surfaced in the UI/API/reports).
- **Single-table workflow.** Each upload becomes one table; there is no
  multi-table join support.
- **CTE validation is a heuristic, not a full parser.** See
  [Security considerations](#security-considerations) above.
- **Anomaly detection** was scoped for this round but not implemented --
  see [Future improvements](#future-improvements).
- **The `/upload` endpoint and the Streamlit uploader each hold exactly one
  active dataset**; there's no per-user session store beyond
  `st.cache_resource` / a single in-memory `AnalyticalDatabase` instance.
- **Query timeout is currently informational.** `QUERY_TIMEOUT_SECONDS`
  exists in config but isn't wired into an actual per-query timeout
  against SQLite (which, for the workloads this app targets, returns fast
  enough that it hasn't mattered in testing) -- would matter more with a
  slower backend or much larger data.
- See [What has and hasn't been executed](#what-has-and-hasnt-been-executed-in-development)
  for which modules are untested in this environment.

## Future improvements

Explicitly *not* implemented -- listed here so they're not confused with
finished work:

- Anomaly detection (outlier counts/flags on a numeric column).
- Numeric-range and multi-value `WHERE` filters ("orders over $500",
  "region in [North, South]").
- A real SQL parser (`sqlglot`) in place of the current
  regex/tokenizer-based validator, once installable.
- Multi-table joins for datasets uploaded as multiple related files.
- A frontend that calls the FastAPI backend over HTTP (with a real
  "backend unavailable" UI state) instead of running the orchestrator
  in-process, for deployments where the two need to scale independently.

## How this differs from the reference project

| Aspect | Reference (NeejiMed/AI-data-analyst) | DataPilot AI |
|---|---|---|
| Data workflow | Fixed synthetic schema, seeded on startup | User uploads any CSV/Excel; schema introspected at runtime, synonym-matched |
| Database | SQLAlchemy ORM over SQLite (dev) / PostgreSQL (prod) | Direct SQLite (default) or DuckDB, no ORM |
| Retrieval | RAG pipeline over ChromaDB + sentence-transformers | None -- schema-grounded prompting only |
| LLM provider | Groq (Llama 3.3 70B) hardcoded | Pluggable: mock / Anthropic / Ollama via one env var |
| SQL safety | Prompt + code-level checks (per its README) | Independent, from-scratch deterministic validator, CTE-aware, with adversarial tests proving it |
| Intent classification | Intent classifier -> SQL agent -> analytics engine (KPI/trend/anomaly/RFM) | 12-intent structured plan (incl. ambiguity detection and profile-only intents) -> templated SQL -> plan-driven metrics engine |
| Evaluation | Listed as a roadmap item (not yet built, per its README) | A working 40-case benchmark across 2 schemas, run and reported here |
| Excel support | Not mentioned | Implemented and tested (openpyxl) |

## Credits

Architectural inspiration: [NeejiMed/AI-data-analyst](https://github.com/NeejiMed/AI-data-analyst)
(MIT License) -- see [`NOTICE.md`](NOTICE.md).

## License

MIT -- see [`LICENSE`](LICENSE).
