"""arq worker: parse a FHIR bundle, chunk it, embed each chunk, write to
Postgres. Runs as the ``service`` actor (see migration 0002) so it can
write documents for any patient, not just one clinician's assignment.

Retries: 3 attempts total, exponential backoff with jitter (1s, 4s, 16s +-
25%). The jitter matters because a transient outage (a Postgres restart, a
network blip) fails every in-flight job at roughly the same instant; without
jitter, all of them would retry at exactly 1s, then all again at exactly
4s, hammering the just-recovered dependency in synchronized waves
("thundering herd"). Randomizing each job's backoff by +-25% spreads those
waves out.

Progress (``processed_count``/``total_count``) and terminal state are
committed after each chunk in their own short transaction specifically so
that killing the worker mid-job leaves a job in a state a restart can
resume metadata for (or at least be visible as: this crashed partway,
here's an error, not silently vanished) -- see
tests/integration/test_ingest_worker_crash_recovery.py.
"""

from __future__ import annotations

import base64
import logging
import random
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

from arq import Retry
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import Settings
from app.db.session import Database
from app.models.document import Document
from app.models.ingest_job import IngestJob
from app.models.patient import Patient
from app.observability.logging import log_event, request_id_var
from app.observability.metrics import ingest_jobs_total
from app.services.embeddings import Embedder

logger = logging.getLogger("app.worker.ingest")

SERVICE_ACTOR_ID = str(uuid.UUID(int=0))
SERVICE_ROLE = "service"

_TEXT_RESOURCE_TYPES = ("DocumentReference", "Observation", "Condition", "MedicationRequest", "Encounter")


def _jittered_backoff(attempt: int, settings: Settings) -> float:
    base = settings.ingest_backoff_base_seconds * (settings.ingest_backoff_multiplier ** (attempt - 1))
    jitter = base * settings.ingest_backoff_jitter_ratio
    return base + random.uniform(-jitter, jitter)


def _extract_patient(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in entries:
        resource: dict[str, Any] = entry.get("resource", {})
        if resource.get("resourceType") == "Patient":
            return resource
    return None


def _resource_text(resource: dict[str, Any]) -> str | None:
    rtype = resource.get("resourceType")
    if rtype == "DocumentReference":
        for content in resource.get("content", []):
            data = content.get("attachment", {}).get("data")
            if data:
                try:
                    return base64.b64decode(data).decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    return None
        return None
    if rtype == "Condition":
        code = resource.get("code", {}).get("text") or resource.get("code", {}).get("coding", [{}])[0].get("display")
        return f"Condition recorded: {code}" if code else None
    if rtype == "MedicationRequest":
        med = resource.get("medicationCodeableConcept", {}).get("text")
        return f"Medication requested: {med}" if med else None
    if rtype == "Observation":
        code = resource.get("code", {}).get("text")
        value = resource.get("valueQuantity", {}).get("value")
        unit = resource.get("valueQuantity", {}).get("unit", "")
        if code and value is not None:
            return f"Observation {code}: {value} {unit}".strip()
        return None
    if rtype == "Encounter":
        etype = resource.get("type", [{}])[0].get("text")
        return f"Encounter: {etype}" if etype else None
    return None


async def process_bundle(ctx: dict[str, Any], job_id: str, bundle: dict[str, Any], request_id: str) -> None:
    request_id_var.set(request_id)
    db: Database = ctx["db"]
    settings: Settings = ctx["settings"]
    embedder: Embedder = ctx["embedder"]
    job_try: int = ctx.get("job_try", 1)

    async with db.session_as(SERVICE_ACTOR_ID, SERVICE_ROLE) as session:
        job = (await session.execute(select(IngestJob).where(IngestJob.id == job_id))).scalar_one()
        job.status = "running"
        job.attempt = job_try

    try:
        entries = bundle.get("entry", [])
        patient_resource = _extract_patient(entries)
        if patient_resource is None:
            raise ValueError("Bundle contains no Patient resource.")

        patient_id = await _upsert_patient(db, patient_resource)

        text_entries = [e for e in entries if e.get("resource", {}).get("resourceType") in _TEXT_RESOURCE_TYPES]
        async with db.session_as(SERVICE_ACTOR_ID, SERVICE_ROLE) as session:
            job = (await session.execute(select(IngestJob).where(IngestJob.id == job_id))).scalar_one()
            job.total_count = len(text_entries)

        for i, entry in enumerate(text_entries):
            resource = entry["resource"]
            text = _resource_text(resource)
            if text:
                await _upsert_document(db, patient_id, resource, text, embedder)
            async with db.session_as(SERVICE_ACTOR_ID, SERVICE_ROLE) as session:
                job = (await session.execute(select(IngestJob).where(IngestJob.id == job_id))).scalar_one()
                job.processed_count = i + 1

        async with db.session_as(SERVICE_ACTOR_ID, SERVICE_ROLE) as session:
            job = (await session.execute(select(IngestJob).where(IngestJob.id == job_id))).scalar_one()
            job.status = "succeeded"
        ingest_jobs_total.labels(status="succeeded").inc()
        log_event(logger, "ingest_job_succeeded", job_id=job_id, request_id=request_id)

    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        if job_try >= settings.ingest_max_retries:
            async with db.session_as(SERVICE_ACTOR_ID, SERVICE_ROLE) as session:
                job = (await session.execute(select(IngestJob).where(IngestJob.id == job_id))).scalar_one()
                job.status = "dead"
                job.error = str(exc)
                job.traceback = tb
            ingest_jobs_total.labels(status="dead").inc()
            log_event(logger, "ingest_job_dead", job_id=job_id, request_id=request_id)
            return

        async with db.session_as(SERVICE_ACTOR_ID, SERVICE_ROLE) as session:
            job = (await session.execute(select(IngestJob).where(IngestJob.id == job_id))).scalar_one()
            job.status = "queued"
            job.error = str(exc)
            job.traceback = tb
        backoff = _jittered_backoff(job_try, settings)
        log_event(
            logger, "ingest_job_retry_scheduled", job_id=job_id, request_id=request_id, duration_ms=backoff * 1000
        )
        raise Retry(defer=backoff) from exc


async def _upsert_patient(db: Database, resource: dict[str, Any]) -> str:
    external_id = resource["id"]
    name = (resource.get("name") or [{}])[0]
    given = " ".join(name.get("given", [])) or "Unknown"
    family = name.get("family") or "Unknown"
    birth_date = resource.get("birthDate")
    gender = resource.get("gender")

    async with db.session_as(SERVICE_ACTOR_ID, SERVICE_ROLE) as session:
        existing = (await session.execute(select(Patient).where(Patient.external_id == external_id))).scalar_one_or_none()
        if existing:
            return str(existing.id)
        patient = Patient(
            id=uuid.uuid4(),
            external_id=external_id,
            given_name=given,
            family_name=family,
            birth_date=datetime.strptime(birth_date, "%Y-%m-%d").date() if birth_date else None,
            gender=gender,
        )
        session.add(patient)
        await session.flush()
        return str(patient.id)


async def _upsert_document(
    db: Database, patient_id: str, resource: dict[str, Any], text: str, embedder: Embedder
) -> None:
    chunk_id = f"bundle::{resource['resourceType']}::{resource['id']}"
    embedding = embedder.embed(text)
    async with db.session_as(SERVICE_ACTOR_ID, SERVICE_ROLE) as session:
        stmt = (
            insert(Document)
            .values(
                id=uuid.uuid4(),
                patient_id=patient_id,
                chunk_id=chunk_id,
                chunk_level="fixed_512",
                encounter_id=resource.get("encounter", {}).get("reference"),
                encounter_date=resource.get("effectiveDateTime") or resource.get("recordedDate") or resource.get("date"),
                encounter_type=resource["resourceType"],
                text=text,
                embedding=embedding,
                source_resource_ids=[resource["id"]],
            )
            .on_conflict_do_update(
                index_elements=["chunk_id"],
                set_={"text": text, "embedding": embedding, "updated_at": datetime.now(UTC)},
            )
        )
        await session.execute(stmt)
