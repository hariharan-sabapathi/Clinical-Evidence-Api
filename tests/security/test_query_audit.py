"""``/query`` is the most sensitive read in the service and, before this
fix, was the one clinical read that wrote no audit row -- a cache hit
returned before any audit code ran at all. This proves both the
freshly-computed path and the cache-hit path each write exactly one row,
in the same transaction as the read they document.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration


async def test_query_writes_one_audit_row(client, app_instance, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.post(
        f"/v1/patients/{world.patient_a.id}/query", json={"q": "what medication is prescribed?"},
        headers=auth_header(token),
    )
    assert r.status_code == 200

    async with app_instance.state.db.session_unscoped() as session:
        rows = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.patient_id == world.patient_a.id, AuditEvent.action == "query"
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_id == world.clinician_a.id
    assert rows[0].resource_type == "query"


class _FixedAnswerClient:
    """A configured (non-retrieval-only) model with a real answer to cache
    -- the retrieval-only floor (see app/services/generation.py) never
    caches a degraded response, so exercising the cache-hit path needs a
    client that actually produces one."""

    model_name = "fixed-answer-test-double"

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        return "Metformin 500mg [fixed_512::a::0]."


async def test_cached_query_writes_its_own_audit_row(client, app_instance, world):
    from app.services.generation import GenerationService

    app_instance.state.generation_service = GenerationService(
        settings=app_instance.state.settings,
        primary_client=_FixedAnswerClient(),
        breaker=app_instance.state.circuit_breaker,
    )

    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    question = {"q": "what medication is prescribed for diabetes?"}

    r1 = await client.post(
        f"/v1/patients/{world.patient_a.id}/query", json=question, headers=auth_header(token)
    )
    assert r1.status_code == 200
    assert r1.json()["served_by"] != "cache"

    r2 = await client.post(
        f"/v1/patients/{world.patient_a.id}/query", json=question, headers=auth_header(token)
    )
    assert r2.status_code == 200
    assert r2.json()["served_by"] == "cache"

    async with app_instance.state.db.session_unscoped() as session:
        query_rows = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.patient_id == world.patient_a.id, AuditEvent.action == "query"
                )
            )
        ).scalars().all()
        cache_rows = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.patient_id == world.patient_a.id, AuditEvent.action == "query_cache_hit"
                )
            )
        ).scalars().all()
    assert len(query_rows) == 1
    assert len(cache_rows) == 1
