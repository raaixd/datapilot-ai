# DataPilot AI — Current Architecture

> **Audit Date:** Phase 0 pre-migration audit  
> **Baseline test result:** 227 passed, 34 subtests passed (1.68 s) — **0 failures**  
> **Purpose:** Document the as-is architecture before any cloud migration touches it.

---

## 1. Project Overview

DataPilot AI is a local, session-based natural-language data-analysis application. A user uploads a CSV or Excel file; DataPilot profiles it, converts analytical questions to SQL, executes the SQL, and returns a grounded insight. It has no cloud dependencies today.

---

## 2. Repository Structure

```
datapilot-ai/
├── app/
│   ├── api/
│   │   ├── main.py            # FastAPI app — thin HTTP wrapper
│   │   ├── schemas.py         # Pydantic request/response models
│   │   ├── session_manager.py # Thread-safe session registry
│   │   └── state.py           # SessionState dataclass
│   ├── agents/
│   │   ├── orchestrator.py    # Central pipeline wiring
│   │   ├── planner.py         # LLM-driven analysis plan builder
│   │   ├── scope_classifier.py# In/out/ambiguous/unsafe gate
│   │   ├── sql_generator.py   # Plan → SQL via LLM
│   │   ├── sql_validator.py   # Deterministic SQL safety checks
│   │   └── intent_hints.py    # Keyword hint constants
│   ├── analytics/
│   │   └── metrics.py         # Post-execution metric computation
│   ├── core/
│   │   ├── config.py          # Dataclass settings from env vars
│   │   └── logging_config.py  # Structured logging setup
│   ├── data/
│   │   ├── database.py        # SQLite/DuckDB analytical DB wrapper
│   │   ├── loader.py          # CSV/XLSX/ZIP ingestion
│   │   ├── profiler.py        # DataFrame profiling engine
│   │   └── column_matcher.py  # Synonym-aware column resolution
│   ├── llm/
│   │   ├── base.py            # LLMClient abstract interface
│   │   ├── factory.py         # Provider selection from settings
│   │   ├── mock_client.py     # Deterministic schema-aware mock (for tests)
│   │   ├── groq_client.py     # Groq (Llama 3) client
│   │   ├── anthropic_client.py# Anthropic + Ollama clients
│   │   ├── freellmapi_client.py # Free API adapter
│   │   └── prompts.py         # Prompt templates (planner, SQL, insight, correction)
│   ├── rag/
│   │   ├── knowledge_base.py  # Curated glossary + validated examples
│   │   └── retriever.py       # Keyword-based context retrieval
│   ├── reports/
│   │   ├── markdown_report.py # Markdown report generator
│   │   └── pdf_report.py      # PDF report generator (ReportLab)
│   └── visualization/
│       └── charts.py          # Plotly chart builder from AnalysisResult
├── frontend/
│   ├── streamlit_app.py       # Streamlit UI (2,000+ lines)
│   └── api_client.py          # HTTP client wrapping FastAPI calls
├── eval/
│   ├── benchmark.py           # 50+ BenchmarkCase definitions (2 datasets)
│   └── run_eval.py            # Evaluation harness
├── tests/                     # 20 test files, 227 tests
├── scripts/
│   ├── demo_cli.py            # CLI demo script
│   ├── run_all.sh
│   └── run_all.ps1
├── data/                      # Sample CSV/XLSX datasets
├── docs/                      # (this file)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pyproject.toml
└── .env.example
```

---

## 3. Current Pipeline Flow

```
User Question
     │
     ▼
[scope_classifier.py]
     │  in_scope / ambiguous / out_of_scope / unsafe
     ▼
[planner.py] → LLM → AnalysisPlan (JSON)
     │
     ▼
[sql_generator.py] → LLM → raw SQL
     │
     ▼
[sql_validator.py] → ValidationResult (deterministic safety checks)
     │  is_valid=True → safe_sql
     ▼
[database.py] → query() → pd.DataFrame
     │
     ├─── ERROR → [sql_generator.correct()] → retry (max 2 attempts)
     │
     ▼
[metrics.py] → compute_result_metrics()
     │
     ▼
[LLM narration] → insight string
     │
     ▼
AnalysisResult → API response
```

---

## 4. What Already Works (Production-Quality)

### 4.1 Core AI Pipeline
- **Scope classification** (`scope_classifier.py`): deterministically classifies questions as `in_scope`, `ambiguous`, `out_of_scope`, or `unsafe` before any LLM call.
- **Structured planning** (`planner.py`): LLM produces a JSON `AnalysisPlan` (intent, table, metric, dimension, filters, chart type) rather than jumping directly to SQL.
- **SQL generation** (`sql_generator.py`): schema-grounded SQL generation from the analysis plan.
- **SQL safety** (`sql_validator.py`): deterministic blocklist of `DROP`, `DELETE`, `INSERT`, `UPDATE`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`, `REVOKE`, `ATTACH`, `PRAGMA`, `EXEC`, etc. Multiple-statement rejection. System table rejection. Automatic LIMIT injection. Table and column existence checks.
- **Self-correcting SQL** (`orchestrator.py`): 2-attempt repair loop. Each corrected query goes through the same safety validator. Failed corrections surface a controlled error message rather than a fabricated answer.
- **Result grounding** (`metrics.py` + narration): metrics computed deterministically from the actual result DataFrame. LLM narration is seeded with actual numbers. `no_data` flag prevents hallucination on empty result sets.
- **RAG / knowledge retrieval** (`rag/`): keyword-based glossary entries + validated example patterns injected into planning and SQL-generation prompts. Relevance-ranked. Not vector-based (deliberate — justified by knowledge base size).

### 4.2 Data Layer
- **Dual-backend analytical DB** (`database.py`): SQLite (default, zero deps) or DuckDB. File-backed, thread-safe per-session databases. Temp files cleaned up on session expiry.
- **Robust data loading** (`loader.py`): CSV with encoding fallbacks (UTF-8-sig → CP1252), XLSX, multi-table ZIP archives.
- **Profiler** (`profiler.py`): deterministic — no LLM involved. Computes: row count, column count, duplicate count, null counts/percentages, inferred semantic types (numeric/categorical/datetime/boolean/text), min/max/mean/std for numeric columns, date parsing checks, cardinality warnings, negative-value-in-money-field warnings.
- **Numeric coercion** (`database.py`): detects string-typed numeric columns (e.g. `"1,234.56"`, `"$500"`) and coerces them before loading — prevents silent metric-column-not-found failures.
- **Column matching** (`column_matcher.py`): synonym-aware concept-to-column resolution (e.g. "order value" → `revenue`).

### 4.3 API & Session Layer
- **FastAPI API** (`api/main.py`): `POST /upload`, `GET /tables/{session_id}`, `POST /query`, `DELETE /session/{session_id}`, `GET /health`.
- **Session isolation** (`session_manager.py`): each upload gets a UUID session. Separate file-backed DB per session. TTL-based expiry (default 2 hours).
- **Pydantic schemas** (`schemas.py`): typed request/response models with field validators.

### 4.4 Frontend
- **Streamlit dashboard** (`streamlit_app.py`): full UI with upload, profiling display, query, chart, SQL viewer, follow-up questions, data quality warnings, analysis history.

### 4.5 LLM Abstraction
- **Provider-agnostic `LLMClient` interface** (`llm/base.py`): one `complete(system, user)` method.
- **Providers**: Mock (deterministic, tests), Groq (Llama 3), Anthropic (Claude), Ollama (local), FreeLLM API.
- **Gemini not yet present** — the target architecture requires it.

### 4.6 Testing & Evaluation
- **227 tests, all passing**: orchestrator end-to-end, SQL validator, SQL correction, scope classifier, session manager, profiler, RAG retriever, RAG prompt integration, report generation, loader, column matcher, metrics, thread safety, table name injection.
- **Evaluation benchmark**: 50+ cases across 2 datasets (sales + ecommerce), categories: aggregation, ranking, trend, grouped comparison, anomaly detection, missing data, duplicate analysis, ambiguous, off-topic, adversarial, scope classification.

---

## 5. What Is Incomplete / Missing

| Area | Gap |
|------|-----|
| **Cloud storage** | No S3 — datasets live in-memory per session, not persisted |
| **Cloud processing** | No Lambda — no event-driven pipeline |
| **Metadata persistence** | No PostgreSQL/RDS — all state is in-process, lost on restart |
| **Gemini LLM** | Not implemented (requirement calls for Gemini as primary) |
| **LLM fallback** | No primary/fallback provider chain (requirement: Gemini → Groq) |
| **Observability** | No CloudWatch integration; logging is local structured only |
| **IAM** | No AWS role/policy design |
| **Infrastructure as Code** | No CDK/Terraform |
| **Projects concept** | No project-level organisation — sessions are anonymous |
| **Dataset versions** | No versioning |
| **Analysis persistence** | Analysis history lives only in Streamlit session state |
| **Report persistence** | Reports generated to local filesystem only |
| **Dataset status states** | No formal UPLOADED/VALIDATING/PROCESSING/READY/FAILED lifecycle |
| **Semantic schema** | No machine-readable semantic column type metadata |
| **Dataset API endpoints** | No `/projects`, `/datasets/{id}/profile`, `/analyses/{id}`, `/reports` |
| **Frontend project structure** | No multi-project dashboard |
| **Visualization grounding** | Chart type selection works but lacks formal compatibility validation |
| **Evaluation expansion** | Benchmark has ~50 cases; target is 100+ with formal score reporting |
| **LOCAL_MODE abstraction** | No storage adapter interface; cloud vs local is implicit |
| **.env completeness** | Missing AWS vars, Gemini key, PRIMARY/FALLBACK LLM config |
| **`.gitignore`** | Incomplete for AWS artifacts |

---

## 6. What Must Not Be Broken

The following are architecturally correct and must be preserved through the migration:

1. **SQL validator** — correct, comprehensive, well-tested. Must remain the enforcement gate.
2. **2-attempt SQL repair loop** — correct design. Must remain the maximum.
3. **Result grounding** — narration only from actual query results. Must not be weakened.
4. **Session-isolated databases** — the file-backed per-session pattern is the correct approach for the current session model.
5. **Scope classifier** — prevents LLM being asked out-of-scope questions before expensive operations run.
6. **RAG retrieval wiring** — context is actually injected into prompts (tested). Must not be severed.
7. **Mock LLM client** — enables tests to run without any API key. Must remain functional.
8. **All 227 tests** — must continue to pass after every implementation step.

---

## 7. Current Dependencies

```
# Core
pandas>=2.1
numpy>=1.26
reportlab>=4.0
openpyxl>=3.1

# Web
fastapi>=0.110
uvicorn[standard]>=0.29
pydantic>=2.6
python-multipart>=0.0.9

# Frontend
streamlit>=1.33
plotly>=5.20

# Testing
pytest>=8.0

# Optional LLM providers
anthropic>=0.25
openai>=1.0

# Optional DB backend
# duckdb>=0.10

# Lint
ruff>=0.4
```

**Missing from requirements.txt for target architecture:**
- `boto3` — AWS SDK
- `google-generativeai` — Gemini client
- `groq` — Groq client (already partially implemented)
- `sqlalchemy>=2.0` — ORM for RDS PostgreSQL
- `alembic` — database migrations
- `psycopg2-binary` — PostgreSQL adapter
- `watchtower` — CloudWatch log handler

---

## 8. Current Database Schema (Per-Session SQLite)

There is no fixed application schema. Each session creates a temp SQLite file and one table per uploaded file. The table schema is dynamically determined from the CSV/Excel column names and types.

No application metadata tables exist (users, projects, datasets, analyses). All metadata is in-process Python state.

---

## 9. Current LLM Architecture

```
LLMClient (base.py — abstract)
     │
     ├── MockLLMClient (deterministic, no network)
     ├── GroqLLMClient (Llama 3 via Groq API)
     ├── AnthropicLLMClient (Claude via Anthropic API)
     ├── OllamaLLMClient (local models via Ollama)
     └── FreeLLMAPIClient (generic OpenAI-compatible)
```

**Calls made per analysis:**
1. `planner.plan()` → 1 LLM call (question → JSON plan)
2. `sql_generator.generate()` → 1 LLM call (plan → SQL)
3. `sql_generator.correct()` → 1 LLM call per repair attempt (max 2)
4. `orchestrator._narrate()` → 1 LLM call (results → insight string)

**Gemini not present.** The target requires adding a Gemini client as primary with Groq as fallback.

---

## 10. Current Deployment Assumptions

- **Local-only**: runs on the developer's machine.
- **Docker**: `Dockerfile` + `docker-compose.yml` support containerised local deployment (API + Streamlit as two services).
- **No cloud infrastructure**: no S3, no Lambda, no RDS, no CloudWatch.
- **No credentials management**: API keys via `.env` file locally.
- **No migrations**: no Alembic, no schema versioning.
- **No production hardening**: CORS is `allow_origins=["*"]`.

---

## 11. Architecture Diagram (Current)

```
┌─────────────────────────────────────────────┐
│                DEVELOPER MACHINE            │
│                                             │
│  ┌─────────────┐    ┌───────────────────┐  │
│  │  Streamlit  │◄───│    FastAPI         │  │
│  │  Frontend   │    │                   │  │
│  └─────────────┘    │  POST /upload     │  │
│                     │  POST /query      │  │
│                     │  GET  /health     │  │
│                     └────────┬──────────┘  │
│                              │             │
│               ┌──────────────▼──────────┐  │
│               │     Orchestrator        │  │
│               │  scope → plan → SQL     │  │
│               │  validate → execute     │  │
│               │  repair → narrate       │  │
│               └──────┬──────────┬───────┘  │
│                      │          │          │
│           ┌──────────▼──┐  ┌───▼───────┐  │
│           │ SQLite/DuckDB│  │LLM Client │  │
│           │(temp file)   │  │(mock/groq/│  │
│           └─────────────┘  │ anthropic)│  │
│                            └───────────┘  │
└─────────────────────────────────────────────┘
```

**Nothing leaves the developer's machine.** No cloud. No persistence across restarts.

---

## 12. Technical Debt & Known Issues

| Item | Location | Severity |
|------|----------|----------|
| `allow_origins=["*"]` CORS | `api/main.py:57` | High — must be restricted for production |
| Groq `groq` package not in requirements.txt | `requirements.txt` | Medium |
| `freellmapi_client.py` uses `openai` package | `llm/` | Low |
| Profiler: no median/quantile statistics | `profiler.py` | Medium — required by target spec |
| Profiler: no text column avg-length/empty-pct | `profiler.py` | Medium — required by target spec |
| Charts: no formal chart-type compatibility validation | `charts.py` | Low |
| No async execution in FastAPI handlers | `api/main.py` | Medium for production |
| Session state lost on restart | `session_manager.py` | High — no persistence |
| LLM narration has no token budget cap | `orchestrator.py` | Medium |
| No request-level tracing/correlation IDs | `api/main.py` | Medium |
