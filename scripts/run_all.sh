#!/usr/bin/env bash
# Convenience launcher for local development: starts the FastAPI backend in
# the background, then the Streamlit frontend in the foreground. This does
# NOT replace running them separately (see README "Running it") -- it's a
# thin wrapper for the common case of wanting both up with one command.
#
# NOTE: this script has not been executed in this project's dev sandbox
# (no fastapi/uvicorn/streamlit installed there -- see README "What has and
# hasn't been executed"). It was written to the documented two-terminal
# commands and should be verified locally.
set -euo pipefail

echo "Starting FastAPI backend on http://localhost:8000 ..."
uvicorn app.api.main:app --host 0.0.0.0 --port 8000 &
API_PID=$!

cleanup() {
    echo "Stopping backend (pid $API_PID)..."
    kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT

sleep 2
echo "Starting Streamlit frontend on http://localhost:8501 ..."
streamlit run frontend/streamlit_app.py
