# Groq API Setup Guide

DataPilot AI supports [Groq](https://groq.com) for high-speed cloud inference using models like `llama-3.3-70b-versatile`.

---

## 1. Get Your Groq API Key

1. Sign up or log in at [Groq Console](https://console.groq.com).
2. Navigate to **API Keys** in the left sidebar.
3. Click **Create API Key**, give it a name (e.g. `datapilot-ai`), and copy the key.

---

## 2. Configure Environment

Create or edit your local `.env` file in the project root (never commit `.env` to version control):

```bash
# Enable the Groq provider
LLM_PROVIDER=groq

# Paste your actual key here (do NOT commit this file to Git):
GROQ_API_KEY=your_groq_api_key_here

# Recommended model
LLM_MODEL=llama-3.3-70b-versatile
```

Supported Groq models:
- `llama-3.3-70b-versatile` (Recommended default: best analytical reasoning and SQL generation)
- `llama-3.1-8b-instant` (Fastest, lower latency)
- `mixtral-8x7b-32768`

---

## 3. Verify Offline / Test Mode

The test suite and evaluation harness (`eval/run_eval.py`) use `LLM_PROVIDER=mock` by default, so they do **not** require a Groq API key to run.

To verify your Groq connection locally after setting `GROQ_API_KEY`:

```bash
# Start Streamlit with your .env loaded
streamlit run frontend/streamlit_app.py
```
Or with FastAPI:
```bash
uvicorn app.api.main:app --reload
```
