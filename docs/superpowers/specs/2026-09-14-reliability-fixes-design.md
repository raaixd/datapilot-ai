# Reliability Fixes Design

## Goal

Correct provider attribution, real-model planning compatibility, sample-value filtering, cross-platform PDF export, deployment dependencies, and API dataset isolation.

## Decisions

- LLM clients expose a `provider_name`; `Orchestrator` uses that runtime value rather than ambient environment configuration.
- Schema descriptions encode sample values as JSON, and the mock parser decodes that representation, preserving punctuation.
- The planning prompt uses the canonical runtime intent names.
- Streamlit creates a named temporary PDF file and removes it after reading its bytes.
- `python-dotenv` becomes an explicit runtime dependency; documentation lists `freellmapi`.
- The API keeps datasets and profiles in a lock-protected registry keyed by `QueryRequest.dataset`, with uploads accepting the same dataset key.

## Verification

Add regression tests before each implementation change. Run targeted tests, the full pytest suite, the eval harness, and Ruff after implementation.
