from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, current_actor, db_session_for_actor
from app.core.errors import ApiError
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.models.care_assignment import CareAssignment
from app.models.patient import Patient
from app.schemas.common import Page
from app.schemas.patients import PatientOut
from app.services.audit import record_access
from app.services.authorization import require_patient_assignment

router = APIRouter(prefix="/v1/patients", tags=["patients"])

_DEFAULT_PAGE_SIZE = 20


def _to_patient_out(p: Patient) -> PatientOut:
    return PatientOut(
        id=str(p.id),
        external_id=p.external_id,
        given_name=p.given_name,
        family_name=p.family_name,
        birth_date=p.birth_date,
        gender=p.gender,
        created_at=p.created_at,
    )


@router.get(
    "",
    response_model=Page[PatientOut],
    summary="List patients the current clinician is assigned to",
    responses={422: {"description": "Malformed cursor"}},
)
async def list_patients(
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=100),
    actor: Actor = Depends(current_actor),
    session: AsyncSession = Depends(db_session_for_actor),
) -> Page[PatientOut]:
    decoded = None
    if cursor is not None:
        try:
            decoded = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise ApiError(422, str(exc), title="Invalid Cursor") from exc

    if actor.role == "admin":
        stmt = select(Patient)
    else:
        stmt = select(Patient).join(CareAssignment, CareAssignment.patient_id == Patient.id).where(
            and_(CareAssignment.clinician_id == actor.id, CareAssignment.revoked_at.is_(None))
        )

    stmt = stmt.order_by(Patient.created_at.asc(), Patient.id.asc())
    if decoded is not None:
        stmt = stmt.where(
            tuple_(Patient.created_at, Patient.id) > (decoded.sort_key, decoded.id)
        )
    stmt = stmt.limit(limit + 1)

    rows = (await session.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))

    return Page(items=[_to_patient_out(p) for p in rows], next_cursor=next_cursor)


@router.get(
    "/{patient_id}",
    response_model=PatientOut,
    summary="Fetch one patient -- scoped to your care assignments",
    responses={403: {"description": "Not assigned to this patient"}, 404: {"description": "Patient not found"}},
)
async def get_patient(
    patient_id: str,
    request: Request,
    actor: Actor = Depends(current_actor),
    session: AsyncSession = Depends(db_session_for_actor),
) -> PatientOut:
    patient = (await session.execute(select(Patient).where(Patient.id == patient_id))).scalar_one_or_none()
    if patient is None:
        raise ApiError(404, "Patient not found.", title="Not Found")

    await require_patient_assignment(session, actor, patient_id)

    await record_access(
        session, request, actor, resource_type="patient", resource_id=str(patient.id),
        action="read", patient_id=patient.id,
    )
    return _to_patient_out(patient)
