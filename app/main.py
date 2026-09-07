"""Application factory + lifespan. Every shared, per-process resource
(DB engine, Redis pool, embedder, circuit breaker, generation service) is
built once here and hung off ``app.state`` -- request code reaches it
through a dependency (app/api/deps.py), never a module-level global, so
tests can spin up an isolated app instance per test without cross-test
state leaking through import-time singletons.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from redis.asyncio import Redis

from app.api.v1 import audit, auth, documents, ingest, patients, query
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.middleware import RequestContextMiddleware
from app.db.session import Database
from app.observability.logging import configure_logging
from app.observability.metrics import render_latest
from app.services.circuit_breaker import CircuitBreaker
from app.services.embeddings import HashingEmbedder
from app.services.generation import GenerationService
from app.services.llm_clients import build_llm_client
from app.services.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger("app.startup")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging()

    app.state.settings = settings
    app.state.db = Database(settings)
    app.state.redis = Redis.from_url(settings.redis_url, socket_timeout=settings.redis_timeout_seconds)
    app.state.arq_redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    app.state.embedder = HashingEmbedder(dim=settings.embedding_dim)
    app.state.rate_limiter = TokenBucketRateLimiter(
        app.state.redis, settings.rate_limit_burst, settings.rate_limit_requests_per_minute
    )
    app.state.circuit_breaker = CircuitBreaker(
        failure_threshold=settings.breaker_failure_threshold, open_seconds=settings.breaker_open_seconds
    )
    primary_client = build_llm_client(settings.llm_model, settings.llm_base_url)
    app.state.generation_service = GenerationService(
        settings=settings,
        primary_client=primary_client,
        breaker=app.state.circuit_breaker,
    )

    logger.info("startup_complete")
    try:
        yield
    finally:
        # Graceful shutdown: stop taking new work, let in-flight requests
        # drain (the ASGI server -- uvicorn's --timeout-graceful-shutdown --
        # handles not accepting new connections; this closes the pools once
        # it hands control back here), then release connections.
        await app.state.arq_redis.aclose()
        await app.state.redis.aclose()
        await app.state.db.dispose()
        logger.info("shutdown_complete")


_OPENAPI_TAGS = [
    {"name": "auth", "description": "OAuth2 password grant and refresh-token rotation."},
    {
        "name": "patients",
        "description": "Patient records, scoped by Postgres RLS to the caller's care assignments.",
    },
    {
        "name": "documents",
        "description": "Clinical document reads and optimistic-locked updates for one patient's chart.",
    },
    {
        "name": "query",
        "description": "Retrieval-grounded Q&A: patient-scoped vector search, PHI redaction, "
        "circuit-breaker-gated generation.",
    },
    {
        "name": "ingest",
        "description": "Async FHIR bundle ingestion -- admin only, idempotent, backed by an arq queue.",
    },
    {
        "name": "audit",
        "description": "Append-only access log, written in the same transaction as the read it records.",
    },
    {"name": "ops", "description": "Liveness, readiness, and Prometheus metrics -- no auth required."},
]

_DESCRIPTION = """\
Retrieval-grounded question answering over a clinical FHIR corpus.
Authorization is enforced by Postgres row-level security, not application
checks. Source: https://github.com/hariharan-sabapathi/clinical-evidence-api

**Demo logins**
- `clinician.a@example.org` / `clinician-a-pass`  (assigned to Patient A)
- `clinician.b@example.org` / `clinician-b-pass`  (assigned to Patient B)
- `auditor@example.org` / `auditor-pass`  (audit log only)
- `admin@example.org` / `admin-pass`  (ingestion only, no patient data access)

**See the authorization model in 60 seconds**
1. `POST /v1/auth/token` as clinician A, then Authorize above with the token
2. `GET /v1/patients` &rarr; Patient A only
3. `POST /v1/patients/{patient_a_id}/query` &rarr; cited evidence
4. `GET /v1/patients/{patient_b_id}/documents` &rarr; 403, body leaks nothing

Generation runs in retrieval-only mode on the public instance (no LLM key
on a free-tier host): `/query` returns `"answer": null, "degraded": true`
alongside the same ranked, cited evidence a live model call would ground
its answer in. Every other layer still runs -- patient-scoped retrieval,
PHI redaction before egress, the breaker.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="Clinical Evidence API",
        description=_DESCRIPTION,
        version="0.1.0",
        openapi_tags=_OPENAPI_TAGS,
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware, header_name=get_settings().request_id_header)
    register_exception_handlers(app)

    app.include_router(auth.router)
    app.include_router(ingest.router)
    app.include_router(patients.router)
    app.include_router(documents.router)
    app.include_router(query.router)
    app.include_router(audit.router)

    @app.get("/healthz", tags=["ops"], summary="Liveness -- no dependency checks")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"], summary="Readiness -- checks Postgres and Redis")
    async def readyz(request: Request) -> Response:
        from sqlalchemy import text

        problems: list[str] = []
        try:
            async with request.app.state.db.session_unscoped() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"postgres: {exc}")
        try:
            await request.app.state.redis.ping()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"redis: {exc}")

        if problems:
            return PlainTextResponse("\n".join(problems), status_code=503)
        return PlainTextResponse("ok", status_code=200)

    @app.get("/metrics", tags=["ops"], summary="Prometheus exposition")
    async def metrics() -> Response:
        payload, content_type = render_latest()
        return Response(content=payload, media_type=content_type)

    return app


app = create_app()
