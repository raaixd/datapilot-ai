# Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make analysis results truthful, robust with real LLMs and literal data values, deployable, cross-platform, and safely isolated by dataset key.

**Architecture:** Client identity is carried through the LLM boundary; planner schema samples use a lossless JSON form. The API owns dataset-specific database/profile state behind a lock, while the frontend uses OS-managed temporary files.

**Tech Stack:** Python 3.11, pandas, FastAPI, Streamlit, pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-reliability-fixes-design.md`

## Global Constraints

- Add regression tests before production changes.
- Preserve existing single-dataset behavior through the `default` dataset key.
- Do not log or expose provider API keys.
- Support Windows and Unix PDF export paths.

---

### Task 1: Make LLM metadata and planning/schema contracts reliable

**Files:**
- Modify: `app/llm/base.py`, `app/llm/mock_client.py`, `app/llm/anthropic_client.py`, `app/llm/freellmapi_client.py`, `app/agents/orchestrator.py`, `app/agents/planner.py`, `app/llm/prompts.py`
- Test: `tests/test_orchestrator.py`, `tests/test_plan_validation.py`

- [ ] Add failing tests for runtime provider identity, canonical real-model comparison intent, and a comma-containing filter value.
- [ ] Run targeted tests and confirm failures.
- [ ] Add provider identities, JSON sample encoding/decoding, and canonical prompt intents.
- [ ] Run targeted tests and confirm they pass.

### Task 2: Make runtime configuration and PDF export portable

**Files:**
- Modify: `requirements.txt`, `README.md`, `.env.example`, `frontend/streamlit_app.py`
- Test: `tests/test_reports.py`

- [ ] Add a failing test for a temporary PDF output path.
- [ ] Replace the fixed `/tmp` filename with a named temporary file lifecycle.
- [ ] Add `python-dotenv` and FreeLLMAPI documentation.
- [ ] Run targeted tests and confirm they pass.

### Task 3: Isolate API datasets by dataset key

**Files:**
- Modify: `app/api/main.py`, `app/api/schemas.py`
- Test: `tests/test_api.py`

- [ ] Add failing API tests proving separate named datasets retain independent query state.
- [ ] Implement a lock-protected dataset registry and dataset-aware upload/query behavior.
- [ ] Run API tests and confirm they pass.

### Task 4: Verify the complete change set

- [ ] Run `pytest -p no:cacheprovider -q`.
- [ ] Run `python eval/run_eval.py` with `PYTHONPATH=.`.
- [ ] Run `ruff check --no-cache app frontend eval scripts tests`.
