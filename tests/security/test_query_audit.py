"""``/query`` is the most sensitive read in the service. This proves it
writes exactly one audit row per call, in the same transaction as the
read it documents.
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
