"""GET /v1/audit -- auditor role only. Auditors can see *that* access
happened (who, when, what resource, what request) but never the clinical
content itself: this endpoint only ever selects from audit_events, which
holds no note text, chunk text, or answer text -- so there's no query
shape here that could leak PHI, structurally, not just by convention.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session_for_actor, require_roles
from app.core.errors import ApiError
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.models.audit import AuditEvent
from app.schemas.audit import AuditEventOut
from app.schemas.common import Page

router = APIRouter(prefix="/v1/audit", tags=["audit"])

_DEFAULT_PAGE_SIZE = 50


@router.get(
    "",
    response_model=Page[AuditEventOut],
    summary="Append-only access log -- auditor and admin roles only",
    dependencies=[Depends(require_roles("auditor", "admin"))],
)
async def list_audit_events(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=200),
    session: AsyncSession = Depends(db_session_for_actor),
) -> Page[AuditEventOut]:
    decoded = None
    if cursor is not None:
        try:
            decoded = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise ApiError(422, str(exc), title="Invalid Cursor") from exc

    stmt = select(AuditEvent).order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
    if decoded is not None:
        stmt = stmt.where(tuple_(AuditEvent.occurred_at, AuditEvent.id) > (decoded.sort_key, decoded.id))
    stmt = stmt.limit(limit + 1)

    rows = (await session.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(last.occurred_at.isoformat(), str(last.id))

    items = [
        AuditEventOut(
            id=str(r.id),
            actor_id=str(r.actor_id),
            actor_role=r.actor_role,
            patient_id=str(r.patient_id) if r.patient_id else None,
            resource_type=r.resource_type,
            resource_id=r.resource_id,
            action=r.action,
            purpose_of_use=r.purpose_of_use,
            request_id=r.request_id,
            occurred_at=r.occurred_at,
        )
        for r in rows
    ]
    return Page(items=items, next_cursor=next_cursor)
