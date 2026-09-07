"""POST /v1/patients/{id}/query and /query/stream.

Retrieval scoping, the semantic cache, PHI redaction, the circuit breaker,
and rate limiting all meet here -- this is the endpoint every security
test in tests/security/ is really about.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, current_actor, db_session_for_actor, get_settings_dep
from app.core.config import Settings
from app.core.errors import ApiError
from app.models.patient import Patient
from app.observability.metrics import cache_hits_total, cache_misses_total, retrieval_duration_seconds
from app.schemas.query import Citation, QueryRequest, QueryResponse
from app.services.audit import record_access
from app.services.authorization import require_patient_assignment
from app.services.generation import AnswerResult
from app.services.rate_limiter import TokenBucketRateLimiter
from app.services.retrieval import retrieve
from app.services.semantic_cache import CacheEntry, SemanticCache, StampedeLock

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


def _query_hash(patient_id: str, question: str) -> str:
    return hashlib.sha256(f"{patient_id}:{question.strip().lower()}".encode()).hexdigest()[:24]


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
    cache: SemanticCache = request.app.state.semantic_cache
    lock: StampedeLock = request.app.state.stampede_lock
    generation = request.app.state.generation_service

    t0 = time.perf_counter()
    query_embedding = embedder.embed(body.q)
    query_hash = _query_hash(patient_id, body.q)

    cached = await cache.lookup(patient_id, query_embedding) if settings.semantic_cache_enabled else None
    if cached is not None:
        cache_hits_total.labels(cache="semantic").inc()
        retrieval_ms = (time.perf_counter() - t0) * 1000
        # A cache hit still delivers clinical information, so it audits
        # exactly like a freshly-computed answer -- returning early here
        # skipped the audit write entirely before this fix.
        await record_access(
            session, request, actor, resource_type="query", resource_id=None,
            action="query_cache_hit", patient_id=patient_id,
        )
        return QueryResponse(
            question=body.q,
            answer=cached.answer,
            citations=[Citation(chunk_id=cid, patient_id=patient_id, score=1.0) for cid in cached.citations],
            grounded=cached.grounded,
            degraded=False,
            served_by="cache",
            retrieval_ms=round(retrieval_ms, 2),
            generation_ms=0.0,
        )
    cache_misses_total.labels(cache="semantic").inc()

    got_lock = await lock.acquire(patient_id, query_hash) if settings.semantic_cache_enabled else False
    if settings.semantic_cache_enabled and not got_lock:
        waited = await lock.wait_for(cache, patient_id, query_embedding, timeout_seconds=settings.llm_call_timeout_seconds + 2)
        if waited is not None:
            retrieval_ms = (time.perf_counter() - t0) * 1000
            await record_access(
                session, request, actor, resource_type="query", resource_id=None,
                action="query_cache_hit", patient_id=patient_id,
            )
            return QueryResponse(
                question=body.q,
                answer=waited.answer,
                citations=[Citation(chunk_id=cid, patient_id=patient_id, score=1.0) for cid in waited.citations],
                grounded=waited.grounded,
                degraded=False,
                served_by="cache",
                retrieval_ms=round(retrieval_ms, 2),
                generation_ms=0.0,
            )
        # Fell through: the holder never finished within the wait window.
        # Proceed to compute directly rather than hang the request forever.

    try:
        top_k = body.top_k or settings.retrieval_top_k
        with retrieval_duration_seconds.labels(stage="vector_search").time():
            retrieved = await retrieve(session, embedder, patient_id, body.q, top_k)
        retrieval_ms = (time.perf_counter() - t0) * 1000

        known_names = await _patient_known_names(session, patient_id)
        t1 = time.perf_counter()
        result = await generation.answer(body.q, retrieved, actor.id, known_names)
        generation_ms = (time.perf_counter() - t1) * 1000

        if settings.semantic_cache_enabled and not result.degraded and result.text is not None:
            await cache.store(
                patient_id,
                CacheEntry(
                    query=body.q,
                    answer=result.text,
                    citations=result.citations,
                    embedding=query_embedding,
                    grounded=result.grounded,
                ),
            )
    finally:
        if got_lock:
            await lock.release(patient_id, query_hash)

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


_SSE_EXAMPLE = (
    'event: token\ndata: {"text": "Metformin "}\n\n'
    'event: token\ndata: {"text": "500mg. "}\n\n'
    'event: done\ndata: {"citations": [{"chunk_id": "fixed_512::enc123::0", "patient_id": '
    '"3f9a6c9e-9c2e-4e77-9c34-6a2f0e9c8b11", "score": 0.87}], "grounded": true, '
    '"degraded": false, "served_by": "primary", "tokens_prompt": 257, "tokens_completion": 14}\n\n'
)


@router.post(
    "/{patient_id}/query/stream",
    summary="SSE token-by-token streaming answer",
    responses={
        200: {
            "description": "text/event-stream of token and done events",
            "content": {"text/event-stream": {"example": _SSE_EXAMPLE}},
        },
        403: {"description": "Not assigned to this patient"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def query_patient_stream(
    patient_id: str,
    body: QueryRequest,
    request: Request,
    actor: Actor = Depends(current_actor),
    session: AsyncSession = Depends(db_session_for_actor),
    settings: Settings = Depends(get_settings_dep),
) -> StreamingResponse:
    await require_patient_assignment(session, actor, patient_id)
    await _enforce_rate_limit(request, actor)

    embedder = request.app.state.embedder
    generation = request.app.state.generation_service
    top_k = body.top_k or settings.retrieval_top_k
    retrieved = await retrieve(session, embedder, patient_id, body.q, top_k)
    known_names = await _patient_known_names(session, patient_id)

    # Written now, in the same transaction as the retrieval above, rather
    # than after the stream finishes: the session dependency stays open for
    # the life of a StreamingResponse, but the client can disconnect or the
    # generator can be cancelled at any point in event_stream() below, and
    # the read this audits already happened by this line regardless.
    await record_access(
        session, request, actor, resource_type="query", resource_id=None,
        action="query", patient_id=patient_id,
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        gen = generation.stream_answer(body.q, retrieved, actor.id, known_names)
        try:
            async for kind, payload in gen:
                if await request.is_disconnected():
                    logger.info("sse_client_disconnected", extra={"task": "llm_call"})
                    await gen.aclose()
                    return
                if kind == "token" and isinstance(payload, str):
                    yield f"event: token\ndata: {json.dumps({'text': payload})}\n\n"
                elif isinstance(payload, AnswerResult):
                    # Same evidence-list shape as the sync endpoint's
                    # `citations` field (see query_patient above), not just
                    # the bare chunk-id strings AnswerResult.citations
                    # carries -- so both response modes document/consume
                    # citations identically.
                    done_payload = {
                        "citations": [
                            {
                                "chunk_id": rc.chunk_id,
                                "patient_id": rc.patient_id,
                                "score": rc.score,
                                "encounter_date": rc.encounter_date,
                                "encounter_type": rc.encounter_type,
                                "section_name": rc.section_name,
                            }
                            for rc in retrieved
                        ],
                        "grounded": payload.grounded,
                        "degraded": payload.degraded,
                        "served_by": payload.served_by,
                        "tokens_prompt": payload.tokens_prompt,
                        "tokens_completion": payload.tokens_completion,
                    }
                    yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"
        finally:
            await gen.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
