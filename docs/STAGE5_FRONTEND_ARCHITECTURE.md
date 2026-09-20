# VERIDEX Stage 5: Frontend Architecture & Product Workflow

## 1. Executive Overview

Stage 5 transforms the existing local/in-process Streamlit interface into a cohesive, production-grade **VERIDEX AI Data Analyst** web application. The frontend communicates with the FastAPI Stage 4 REST API, providing a structured product workflow:

```
Upload Dataset  ──>  Create/Select Project  ──>  Dataset Processing  ──>  Dataset Profile
                                                                               │
                                                                               ▼
Reports & Export  <──  Verified SQL  <──  Grounded Insight  <──  AI Analyst Workspace
```

---

## 2. Current vs. Target Frontend Architecture

### Current Architecture (Legacy)
- **Framework:** Streamlit (`frontend/streamlit_app.py`, ~785 LOC).
- **Execution Mode:** Primarily in-process (allocating temporary SQLite tables and directly instantiating `Orchestrator` and `DataProfiler`).
- **API Client:** Minimal `VeridexApiClient` in `frontend/api_client.py` only wrapping legacy `/upload`, `/query`, and `/health` endpoints.
- **State Model:** In-memory dictionary stored in `st.session_state.tables` without persistent project, dataset, or report entity abstractions.
- **Visual Design:** Dark-themed Hermes-inspired styling, custom CSS variables (`--dp-bg: #090B0E`, `--dp-accent: #38BDF8`), styled buttons, and tabs.

### Target Architecture (Stage 5)
- **Framework:** Streamlit, retaining the sleek dark UI design and avoiding unnecessary frontend framework churn.
- **Architecture Model:** Dual-mode client with first-class REST API integration:
  - **API Mode (Default / Production):** All state, ingestion, profiling, query execution, reports, and observability metrics are driven via the Stage 4 FastAPI endpoints.
  - **In-Process Mode (Fallback / Offline):** Preserved for standalone zero-dependency development and testing without running the web server.
- **Extended API Client (`frontend/api_client.py`):** Fully typed methods covering Projects, Datasets, Profiles, Analyses, Reports, and Observability.
- **State Management:** Project-centric and dataset-centric session state with persistent database IDs.

---

## 3. VERIDEX Product Structure

The application is organized into 7 functional product areas accessible via top-level workspace tabs:

| Workspace Area | Purpose & User Actions | Associated Stage 4 API Endpoints |
| :--- | :--- | :--- |
| **A. Projects** | List, create, and switch between analytical projects. | `GET /projects`, `POST /projects`, `GET /projects/{id}` |
| **B. Dataset Workspace** | Ingest tabular data (CSV, XLSX, ZIP) into the active project; observe real-time processing status. | `POST /projects/{id}/datasets`, `GET /datasets/{id}`, `GET /datasets/{id}/status` |
| **C. Dataset Profile** | Inspect deterministic data profiling, quality warnings, and semantic schema metadata. | `GET /datasets/{id}/profile` |
| **D. AI Analyst** | Submit natural-language analytical questions; inspect analysis plans, generated SQL, preview rows, metrics, and chart recommendations. | `POST /datasets/{id}/analyze` |
| **E. Verified SQL & Audit** | Detailed inspection of executed SQL, syntax/AST validation, retry counts, repair histories, and error classifications. | `GET /analyses/{id}/sql`, `GET /analyses/{id}/results` |
| **F. Reports** | Compile grounded 10-section analytical reports, inspect markdown renderings, and download exports. | `POST /reports`, `GET /reports/{id}`, `GET /reports` |
| **G. Observability** | Operational telemetry displaying server health, request throughput, error rates, active queries, and latency percentiles (`p50`, `p90`, `p99`). | `GET /health`, `GET /ready`, `GET /metrics` |

---

## 4. API Integration Architecture

All network communication is encapsulated in `frontend/api_client.py` (`VeridexApiClient`). No raw `urllib` or HTTP calls are scattered across UI rendering components.

```
┌─────────────────────────────────────────────────────────┐
│              Streamlit UI Component Layer                │
│ (ProjectView, DatasetView, AnalystView, Observability)   │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│             VeridexApiClient (api_client.py)            │
│  - projects (create, list, get)                         │
│  - datasets (upload, get, status, profile)              │
│  - analyses (analyze, get, sql, results)                │
│  - reports (create, list, get)                          │
│  - system (health, ready, metrics)                      │
│  - Error handling (APIClientError, ConnectionError)     │
└────────────────────────────┬────────────────────────────┘
                             │ HTTP/JSON
                             ▼
┌─────────────────────────────────────────────────────────┐
│              FastAPI Backend (app/api/main.py)           │
│  Routers: projects, datasets, analyses, reports, system │
└─────────────────────────────────────────────────────────┘
```

### Error & Exception Handling
- **`ConnectionError`**: Display friendly alert ("Cannot reach VERIDEX API at http://localhost:8000. Start backend with `uvicorn app.api.main:app` or switch to In-Process Mode.")
- **`APIClientError` (409 Conflict)**: Inform user that project name already exists.
- **`APIClientError` (404 Not Found)**: Inform user that the requested entity was not found.
- **`APIClientError` (422 / 400)**: Display validation message from backend response body.

---

## 5. State Management Approach

Streamlit reruns the script on each user interaction. State persistence across reruns is managed via `st.session_state`:

- `st.session_state.api_mode`: Boolean flag toggling between REST API and In-Process modes.
- `st.session_state.api_base_url`: Base URL of the backend (default: `http://localhost:8000` or `API_BASE_URL` environment variable).
- `st.session_state.active_project_id`: ID of the currently selected project.
- `st.session_state.active_dataset_id`: ID of the currently selected dataset.
- `st.session_state.projects_cache`: List of loaded projects.
- `st.session_state.project_datasets`: Mapping of `project_id -> list[DatasetMetadata]`.
- `st.session_state.dataset_profiles`: Mapping of `dataset_id -> DatasetProfileResponse`.
- `st.session_state.analysis_history`: List of executed analysis runs with full audit information.
- `st.session_state.last_analysis_result`: Most recent `AnalyzeResponse`.
- `st.session_state.reports_cache`: List of reports fetched from `GET /reports`.

---

## 6. Important Design Decisions

1. **Retain Streamlit Framework:** Preserving the existing Python Streamlit codebase avoids rewriting working functionality, guarantees instant portability across local environments, and maintains full compatibility with existing Plotly charts.
2. **Deterministic Data Grounding:** The UI displays exactly what the backend computes:
   - Descriptive statistics are drawn from `DataProfile` and `SemanticSchema`.
   - Visualizations are built strictly from `result_preview` and `chart_type` returned by `POST /datasets/{id}/analyze`.
   - SQL queries are explicitly labeled as **Verified Executed SQL**.
3. **Graceful Degradation:** If the API backend is not reachable, the user is offered a single-click switch to In-Process mode with zero data loss.
4. **Comprehensive Observability:** Metrics from `GET /metrics` (`p50`, `p90`, `p99`, error rate, active queries) are rendered in a dedicated developer/admin dashboard section.
