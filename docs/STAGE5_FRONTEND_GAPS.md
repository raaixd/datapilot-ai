# VERIDEX Stage 5: Backend API Gaps & Future Enhancements

As instructed by Stage 5 architectural governance ("If the current API does not expose sufficient history functionality, do NOT invent an endpoint. Document the missing capability in: docs/STAGE5_FRONTEND_GAPS.md"), this document tracks API endpoints that would enhance frontend persistence across browser reloads without altering the established Stage 4 REST contract.

---

## 1. Analysis History Listing Endpoint

### Missing Capability
The Stage 4 REST API currently provides:
- `POST /datasets/{dataset_id}/analyze` (run analysis)
- `GET /analyses/{analysis_id}` (retrieve single analysis run)
- `GET /analyses/{analysis_id}/sql` (retrieve executed SQL and repair history)
- `GET /analyses/{analysis_id}/results` (retrieve result preview and metrics)

However, there is no public endpoint to list historical analysis runs for a dataset:
- Proposed endpoint: `GET /datasets/{dataset_id}/analyses?limit=50&offset=0`

### Current Frontend Mitigation
The frontend tracks analysis IDs and responses in the user's active session state (`st.session_state.analysis_history`), allowing inspection of verified SQL, retry counts, metrics, and insights via `GET /analyses/{analysis_id}`. In a future stage, adding `GET /datasets/{dataset_id}/analyses` to `app/api/routers/analyses.py` will enable cross-session analysis persistence.

---

## 2. Project Datasets Listing Endpoint

### Missing Capability
The Stage 4 REST API currently provides:
- `POST /projects/{project_id}/datasets` (upload and ingest dataset)
- `GET /datasets/{dataset_id}` (get dataset metadata)
- `GET /datasets/{dataset_id}/status` (get processing status)
- `GET /datasets/{dataset_id}/profile` (get profile statistics and semantic schema)

However, there is no dedicated endpoint to list all datasets belonging to a project:
- Proposed endpoint: `GET /projects/{project_id}/datasets`

### Current Frontend Mitigation
The frontend caches uploaded datasets within `st.session_state.project_datasets[project_id]` upon upload, immediately displaying metadata and polling `GET /datasets/{dataset_id}/status` until `READY`. In a future stage, exposing `GET /projects/{project_id}/datasets` backed by `DatasetRepository.list_datasets(project_id=...)` will allow listing datasets across separate user sessions.
