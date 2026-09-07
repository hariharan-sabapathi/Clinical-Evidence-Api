"""Kill the worker mid-job (simulated here by making one resource's
processing raise), restart it: the job must land in a real terminal state
--``dead`` with a readable error -- after exhausting retries, and progress
(``processed_count``) made before the crash must not be lost when the job
is retried. It should never simply vanish (stay ``running`` forever, or
disappear from the table).
"""

from __future__ import annotations

import uuid

import pytest
from arq import Retry as ArqRetry
from sqlalchemy import select

from app.models.ingest_job import IngestJob
from app.workers.ingest import SERVICE_ACTOR_ID, process_bundle

pytestmark = pytest.mark.integration

_BUNDLE = {
    "resourceType": "Bundle",
    "entry": [
        {"resource": {"resourceType": "Patient", "id": "crash-p-1", "name": [{"given": ["Crash"], "family": "Test"}]}},
        {"resource": {"resourceType": "Condition", "id": "c1", "code": {"text": "Condition One"}}},
        {"resource": {"resourceType": "Condition", "id": "c2", "code": {"text": "Condition Two"}}},
        {"resource": {"resourceType": "Condition", "id": "c3", "code": {"text": "Condition Three"}}},
    ],
}


async def _make_job(app_instance) -> str:
    job_id = uuid.uuid4()
    async with app_instance.state.db.session_as(SERVICE_ACTOR_ID, "service") as session:
        session.add(IngestJob(id=job_id, idempotency_key=None, request_id="test-request", status="queued"))
    return str(job_id)


async def _get_job(app_instance, job_id: str) -> IngestJob:
    async with app_instance.state.db.session_unscoped() as session:
        return (await session.execute(select(IngestJob).where(IngestJob.id == job_id))).scalar_one()


async def test_job_reaches_dead_after_exhausting_retries_with_readable_error(app_instance, monkeypatch):
    job_id = await _make_job(app_instance)

    def _always_fails(resource):
        raise RuntimeError("simulated worker crash mid-chunk")

    import app.workers.ingest as ingest_module

    monkeypatch.setattr(ingest_module, "_resource_text", _always_fails)

    ctx = {"db": app_instance.state.db, "settings": app_instance.state.settings, "embedder": app_instance.state.embedder}

    for attempt in (1, 2):
        ctx["job_try"] = attempt
        with pytest.raises(ArqRetry):
            await process_bundle(ctx, job_id, _BUNDLE, "test-request")
        job = await _get_job(app_instance, job_id)
        assert job.status == "queued"
        assert job.attempt == attempt
        assert "simulated worker crash" in job.error

    ctx["job_try"] = app_instance.state.settings.ingest_max_retries
    await process_bundle(ctx, job_id, _BUNDLE, "test-request")  # final attempt: must NOT raise

    job = await _get_job(app_instance, job_id)
    assert job.status == "dead"
    assert "simulated worker crash" in job.error
    assert job.traceback is not None and "RuntimeError" in job.traceback


async def test_partial_progress_is_preserved_across_a_retry(app_instance, monkeypatch):
    """Fail only on the *second* text resource so the first one's
    processing genuinely commits before the crash -- proving progress
    isn't rolled back or lost when the job is retried."""
    job_id = await _make_job(app_instance)

    import app.workers.ingest as ingest_module

    original = ingest_module._resource_text
    call_state = {"count": 0}

    def _fail_on_second_call(resource):
        call_state["count"] += 1
        if call_state["count"] == 2:
            raise RuntimeError("crash on second resource")
        return original(resource)

    monkeypatch.setattr(ingest_module, "_resource_text", _fail_on_second_call)

    ctx = {"db": app_instance.state.db, "settings": app_instance.state.settings, "embedder": app_instance.state.embedder, "job_try": 1}
    with pytest.raises(ArqRetry):
        await process_bundle(ctx, job_id, _BUNDLE, "test-request")

    job = await _get_job(app_instance, job_id)
    assert job.processed_count == 1  # the first resource's progress survived the crash
    assert job.total_count == 3

    # Recovery: retry without the injected failure picks up and finishes.
    monkeypatch.setattr(ingest_module, "_resource_text", original)
    ctx["job_try"] = 2
    await process_bundle(ctx, job_id, _BUNDLE, "test-request")
    job = await _get_job(app_instance, job_id)
    assert job.status == "succeeded"
    assert job.processed_count == 3
