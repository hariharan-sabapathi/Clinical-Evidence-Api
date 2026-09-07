# Multi-stage build for the clinical-evidence-api service (app/).
# The retrieval-library eval harness has its own image -- see
# Dockerfile.eval-harness -- this one is the production HTTP service.

FROM python:3.12-slim AS base

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir --user ".[service]"

FROM base AS runtime

RUN useradd --create-home --uid 1000 appuser
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/src

COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/seed_demo_data.py scripts/seed_demo_data.py
COPY src/ src/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"

# Graceful shutdown (see ARCHITECTURE.md "Reliability"): uvicorn's
# --timeout-graceful-shutdown drains in-flight requests before the process
# exits on SIGTERM, instead of the default abrupt teardown.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
