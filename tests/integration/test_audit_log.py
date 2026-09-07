"""audit_events is append-only at the database level (migration 0003
revokes UPDATE/DELETE from the runtime role), and every clinical read
produces exactly one audit row, written in the same transaction as the
read it records.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.models.audit import AuditEvent
from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration


async def test_update_on_audit_events_is_rejected_by_the_database(app_instance, world):
    async with app_instance.state.db.session_as(str(world.clinician_a.id), "clinician") as session:
        session.add(
            AuditEvent(
                id=uuid.uuid4(), actor_id=world.clinician_a.id, actor_role="clinician",
                resource_type="patient", resource_id=str(world.patient_a.id), action="read",
                request_id="seed",
            )
        )

    async with app_instance.state.db.session_unscoped() as session:
        with pytest.raises(DBAPIError, match="permission denied"):
            await session.execute(update(AuditEvent).values(action="tampered"))


async def test_delete_on_audit_events_is_rejected_by_the_database(app_instance, world):
    async with app_instance.state.db.session_unscoped() as session:
        with pytest.raises(DBAPIError, match="permission denied"):
            await session.execute(text("DELETE FROM audit_events"))


async def test_reading_patient_documents_produces_exactly_one_audit_row(client, app_instance, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.get(f"/v1/patients/{world.patient_a.id}/documents", headers=auth_header(token))
    assert r.status_code == 200

    async with app_instance.state.db.session_unscoped() as session:
        rows = (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.patient_id == world.patient_a.id, AuditEvent.action == "list"
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_id == world.clinician_a.id
    assert rows[0].resource_type == "document_collection"
