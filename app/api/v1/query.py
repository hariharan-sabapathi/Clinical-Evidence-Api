"""POST /v1/patients/{id}/query.

Retrieval scoping, PHI redaction, the circuit breaker, and rate limiting
all meet here -- this is the endpoint every security test in
tests/security/ is really about.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, current_actor, db_session_for_actor, get_settings_dep
from app.core.config import Settings
from app.core.errors import ApiError
from app.models.patient import Patient
from app.observability.metrics import retrieval_duration_seconds
from app.schemas.query import Citation, QueryRequest, QueryResponse
from app.services.audit import record_access
from app.services.authorization import require_patient_assignment
from app.services.rate_limiter import TokenBucketRateLimiter
from app.services.retrieval import retrieve

router = APIRouter(prefix="/v1/patients", tags=["query"])
logger = logging.getLogger("app.query")


async def _enforce_rate_limit(request: Request, actor: Actor) -> None:
    limiter: TokenBucketRateLimiter = request.app.state.rate_limiter
    result = await limiter.allow(actor.id)
    if not result.allowed:
        from app.observability.metrics import rate_limit_rejections_total

        rate_limit_rejections_total.labels(actor_role=actor.role).inc()
        raise ApiError(
            429,
            "Rate limit exceeded.",
            title="Too Many Requests",
            extra={"retry_after": result.retry_after_seconds},
            headers={"Retry-After": str(result.retry_after_seconds)},
        )


async def _patient_known_names(session: AsyncSession, patient_id: str) -> list[str]:
    patient = (await session.execute(select(Patient).where(Patient.id == patient_id))).scalar_one_or_none()
    if patient is None:
        return []
    return [f"{patient.given_name} {patient.family_name}", patient.given_name, patient.family_name]


@router.post(
    "/{patient_id}/query",
    response_model=QueryResponse,
    summary="Retrieval-grounded answer with citations, scoped to this patient",
    responses={
        403: {"description": "Not assigned to this patient"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def query_patient(
    patient_id: str,
    body: QueryRequest,
    request: Request,
    actor: Actor = Depends(current_actor),
    session: AsyncSession = Depends(db_session_for_actor),
    settings: Settings = Depends(get_settings_dep),
) -> QueryResponse:
    await require_patient_assignment(session, actor, patient_id)
    await _enforce_rate_limit(request, actor)

    embedder = request.app.state.embedder
    generation = request.app.state.generation_service

    t0 = time.perf_counter()
    top_k = body.top_k or settings.retrieval_top_k
    with retrieval_duration_seconds.labels(stage="vector_search").time():
        retrieved = await retrieve(session, embedder, patient_id, body.q, top_k)
    retrieval_ms = (time.perf_counter() - t0) * 1000

    known_names = await _patient_known_names(session, patient_id)
    t1 = time.perf_counter()
    result = await generation.answer(body.q, retrieved, actor.id, known_names)
    generation_ms = (time.perf_counter() - t1) * 1000

    # Citations are always the full ranked evidence list, not just the
    # subset the model happened to cite inline -- in degraded mode
    # (breaker open) this is the entire response: ranked evidence with no
    # generated answer. Every entry is drawn from `retrieved`, which
    # retrieve() already scoped to this patient_id inside the SQL query --
    # there is no code path here that can add a citation for a different
    # patient. See tests/security/test_retrieval_scoping.py.
    citations = [
        Citation(
            chunk_id=rc.chunk_id,
            patient_id=rc.patient_id,
            score=rc.score,
            encounter_date=rc.encounter_date,
            encounter_type=rc.encounter_type,
            section_name=rc.section_name,
        )
        for rc in retrieved
    ]

    await record_access(
        session, request, actor, resource_type="query", resource_id=None,
        action="query", patient_id=patient_id,
    )
    return QueryResponse(
        question=body.q,
        answer=result.text,
        citations=citations,
        grounded=result.grounded,
        degraded=result.degraded,
        served_by=result.served_by,
        retrieval_ms=round(retrieval_ms, 2),
        generation_ms=round(generation_ms, 2) if not result.degraded else 0.0,
    )
