"""POST /v1/ingest/bundles: validate the FHIR envelope synchronously,
enqueue the real work, return 202 immediately. The synchronous validation
is deliberately shallow (does this parse as a Bundle with entries?) --
deep parsing/chunking/embedding happens in the worker so a slow or
malformed downstream resource can't turn a client's POST into a
multi-second blocking call.
"""

from __future__ import annotations

import uuid

from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, db_session_unscoped, get_settings_dep, require_roles
from app.core.config import Settings
from app.core.errors import ApiError
from app.models.ingest_job import IngestJob
from app.schemas.ingest import FhirBundleRequest, IngestAcceptedResponse, IngestJobOut
from app.services.idempotency import check_and_reserve, store_response

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


@router.post(
    "/bundles",
    status_code=202,
    response_model=IngestAcceptedResponse,
    summary="Accept a FHIR bundle for async processing -- admin only, requires Idempotency-Key",
    responses={
        202: {"description": "Accepted for async processing"},
        400: {"description": "Idempotency-Key header is required"},
        422: {"description": "Idempotency-Key reused with a different body, or malformed bundle"},
    },
)
async def ingest_bundle(
    bundle: FhirBundleRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    actor: Actor = Depends(require_roles("admin")),  # noqa: B008
    settings: Settings = Depends(get_settings_dep),
    session: AsyncSession = Depends(db_session_unscoped),
) -> dict[str, Any]:
    if not idempotency_key:
        raise ApiError(400, "Idempotency-Key header is required for this endpoint.", title="Bad Request")

    if bundle.resourceType != "Bundle":
        raise ApiError(422, "resourceType must be 'Bundle'.", title="Unprocessable Entity")

    raw_body = bundle.model_dump_json().encode("utf-8")
    replay = await check_and_reserve(session, idempotency_key, raw_body, settings.idempotency_key_ttl_seconds)
    if replay is not None:
        response.status_code = replay.status_code
        response.headers["Location"] = f"/v1/ingest/jobs/{replay.body['job_id']}"
        return replay.body

    job_id = uuid.uuid4()
    request_id = request.state.request_id
    job = IngestJob(id=job_id, idempotency_key=idempotency_key, request_id=request_id, status="queued")
    session.add(job)
    await session.flush()

    redis = request.app.state.arq_redis
    await redis.enqueue_job("process_bundle", str(job_id), bundle.model_dump(), request_id)

    body = {"job_id": str(job_id), "status": "queued"}
    await store_response(session, idempotency_key, raw_body, 202, body, settings.idempotency_key_ttl_seconds)

    response.headers["Location"] = f"/v1/ingest/jobs/{job_id}"
    return body


@router.get(
    "/jobs/{job_id}",
    response_model=IngestJobOut,
    summary="Job status: queued, running, succeeded, failed, or dead",
    responses={404: {"description": "Job not found"}},
)
async def get_ingest_job(
    job_id: str,
    actor: Actor = Depends(require_roles("admin")),  # noqa: B008
    session: AsyncSession = Depends(db_session_unscoped),
) -> IngestJobOut:
    job = (await session.execute(select(IngestJob).where(IngestJob.id == job_id))).scalar_one_or_none()
    if job is None:
        raise ApiError(404, "Ingest job not found.", title="Not Found")
    return IngestJobOut(
        job_id=str(job.id),
        status=job.status,
        total_count=job.total_count,
        processed_count=job.processed_count,
        attempt=job.attempt,
        error=job.error,
    )
