"""Every read of clinical content writes an audit_events row in the *same*
transaction as the read. If the audit write fails, the whole transaction
rolls back and the read fails too -- the client gets a 500, not the
clinical data.

That's a deliberate tradeoff of availability for auditability: in a
regulated clinical context, "the clinician saw the note but there's no
record that they did" is a worse failure than "the clinician's request
failed and they retried it". See docs/adr/0005-audit-write-in-read-
transaction.md.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor
from app.models.audit import AuditEvent


async def record_access(
    session: AsyncSession,
    request: Request,
    actor: Actor,
    *,
    resource_type: str,
    resource_id: str | None,
    action: str,
    patient_id: str | uuid.UUID | None = None,
    purpose_of_use: str = "treatment",
) -> None:
    event = AuditEvent(
        id=uuid.uuid4(),
        actor_id=uuid.UUID(actor.id),
        actor_role=actor.role,
        patient_id=uuid.UUID(str(patient_id)) if patient_id else None,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        action=action,
        purpose_of_use=purpose_of_use,
        request_id=getattr(request.state, "request_id", "unknown"),
        ip=_client_ip(request),
    )
    session.add(event)
    # Flush now (not just add-and-let-commit-handle-it) so a constraint
    # violation on the audit row surfaces here, inside the same request,
    # rather than silently at a later, unrelated flush.
    await session.flush()


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def audit_metadata(**kwargs: Any) -> dict[str, Any]:
    return kwargs
