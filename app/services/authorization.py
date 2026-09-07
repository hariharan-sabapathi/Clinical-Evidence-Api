from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor
from app.core.errors import ApiError
from app.models.care_assignment import CareAssignment


async def require_patient_assignment(session: AsyncSession, actor: Actor, patient_id: str) -> None:
    """Explicit, application-level check used *in addition to* Postgres RLS
    (migration 0002). Two independent layers on purpose: RLS alone would
    turn "not your patient" into a silent empty result set instead of a
    403, and an application check alone would be one code path away from a
    leak the next time someone adds a new query. See "Authorization model"
    in the README."""
    if actor.role == "admin":
        return
    assignment = (
        await session.execute(
            select(CareAssignment).where(
                and_(
                    CareAssignment.clinician_id == actor.id,
                    CareAssignment.patient_id == patient_id,
                    CareAssignment.revoked_at.is_(None),
                )
            )
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise ApiError(403, "Not permitted to access this patient's records.", title="Forbidden")
