# Convenience launcher for local development on Windows PowerShell: starts
# the FastAPI backend in a new window, then the Streamlit frontend in this
# window. Does NOT replace running them separately (see README "Running
# it") -- this is a thin wrapper for the common case of wanting both up
# with one command.
#
# NOTE: this script has not been executed in this project's dev sandbox (no
# fastapi/uvicorn/streamlit installed there, and no Windows environment
# available -- see README "What has and hasn't been executed"). It was
# written to the documented two-terminal commands and should be verified
# locally on Windows before relying on it.

Write-Host "Starting FastAPI backend on http://localhost:8000 in a new window..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "uvicorn app.api.main:app --host 0.0.0.0 --port 8000"

Start-Sleep -Seconds 2

Write-Host "Starting Streamlit frontend on http://localhost:8501 ..."
streamlit run frontend/streamlit_app.py
