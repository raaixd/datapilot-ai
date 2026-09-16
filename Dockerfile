FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV LLM_PROVIDER=mock \
    DATABASE_BACKEND=sqlite \
    PYTHONUNBUFFERED=1

# Non-root user (round 5: was previously missing -- containers should not
# run as root unless there's a specific reason to).
RUN useradd --create-home --uid 1000 datapilot
USER datapilot

EXPOSE 8000

# Healthcheck against the API's own /health endpoint (round 5: was
# previously missing). Uses Python's stdlib urllib instead of curl/wget so
# no extra apt packages are needed in the slim base image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
