from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, current_actor, db_session_for_actor
from app.core.errors import ApiError
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.models.document import Document
from app.schemas.common import Page
from app.schemas.documents import DocumentOut, DocumentPatchRequest
from app.services.audit import record_access
from app.services.authorization import require_patient_assignment

router = APIRouter(prefix="/v1/patients", tags=["documents"])

_DEFAULT_PAGE_SIZE = 20


def _to_document_out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=str(d.id),
        patient_id=str(d.patient_id),
        chunk_id=d.chunk_id,
        chunk_level=d.chunk_level,
        encounter_id=d.encounter_id,
        encounter_date=d.encounter_date,
        encounter_type=d.encounter_type,
        section_name=d.section_name,
        text=d.text,
        version=d.version,
        reviewed=d.reviewed,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


@router.get(
    "/{patient_id}/documents",
    response_model=Page[DocumentOut],
    summary="Clinical documents for a patient, cursor-paginated",
    responses={403: {"description": "Not assigned to this patient"}, 422: {"description": "Malformed cursor"}},
)
async def list_documents(
    patient_id: str,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_PAGE_SIZE, ge=1, le=100),
    actor: Actor = Depends(current_actor),
    session: AsyncSession = Depends(db_session_for_actor),
) -> Page[DocumentOut]:
    # Explicit check first (defense in depth): RLS also scopes the SELECT
    # below to assigned patients, but without this check an unassigned
    # patient with zero documents would look identical to "assigned, no
    # documents yet" -- a 200 with an empty list -- instead of the 403 an
    # authorization boundary should return. See "Authorization model" in
    # the README for why the check happens twice, at two layers.
    await require_patient_assignment(session, actor, patient_id)

    decoded = None
    if cursor is not None:
        try:
            decoded = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise ApiError(422, str(exc), title="Invalid Cursor") from exc

    stmt = select(Document).where(Document.patient_id == patient_id)
    stmt = stmt.order_by(Document.created_at.asc(), Document.id.asc())
    if decoded is not None:
        stmt = stmt.where(tuple_(Document.created_at, Document.id) > (decoded.sort_key, decoded.id))
    stmt = stmt.limit(limit + 1)

    rows = (await session.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))

    await record_access(
        session, request, actor, resource_type="document_collection", resource_id=patient_id,
        action="list", patient_id=patient_id,
    )
    return Page(items=[_to_document_out(d) for d in rows], next_cursor=next_cursor)


@router.patch(
    "/{patient_id}/documents/{doc_id}",
    response_model=DocumentOut,
    summary="Update a document -- requires If-Match with the current ETag; 409 on version conflict",
    responses={
        403: {"description": "Not assigned to this patient"},
        404: {"description": "Document not found"},
        409: {"description": "Version conflict -- refetch and retry with the current ETag"},
        428: {"description": "If-Match header is required"},
    },
)
async def patch_document(
    patient_id: str,
    doc_id: str,
    body: DocumentPatchRequest,
    response: Response,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor: Actor = Depends(current_actor),
    session: AsyncSession = Depends(db_session_for_actor),
) -> DocumentOut:
    await require_patient_assignment(session, actor, patient_id)

    if not if_match:
        raise ApiError(428, "If-Match header is required for updates.", title="Precondition Required")
    expected_version = _parse_etag(if_match)

    doc = (
        await session.execute(select(Document).where(Document.id == doc_id, Document.patient_id == patient_id))
    ).scalar_one_or_none()
    if doc is None:
        raise ApiError(404, "Document not found.", title="Not Found")

    values: dict[str, Any] = {}
    if body.text is not None:
        values["text"] = body.text
    if body.reviewed is not None:
        values["reviewed"] = body.reviewed
    if not values:
        response.headers["ETag"] = f'"{doc.version}"'
        return _to_document_out(doc)

    values["version"] = Document.version + 1
    result = await session.execute(
        update(Document)
        .where(Document.id == doc_id, Document.version == expected_version)
        .values(**values)
        .returning(Document)
        # `doc` above is already loaded in this transaction's identity map.
        # Without populate_existing, SQLAlchemy's ORM-enabled UPDATE...RETURNING
        # leaves an already-present identity-mapped object's attributes as
        # they were *before* the UPDATE rather than refreshing them from the
        # RETURNING row -- silently handing back stale data on a real write.
        .execution_options(populate_existing=True)
    )
    updated = result.scalar_one_or_none()
    if updated is None:
        # Zero rows matched: either the id doesn't exist under this
        # condition anymore or -- the interesting case -- someone else's
        # concurrent PATCH already bumped the version. WHERE id=:id AND
        # version=:version is the entire optimistic-locking mechanism: the
        # UPDATE is atomic, so under concurrent writers exactly one commits
        # and every other one lands here.
        raise ApiError(
            409,
            "Document was modified concurrently. Refetch and retry with the current ETag.",
            title="Conflict",
        )

    await record_access(
        session, request, actor, resource_type="document", resource_id=doc_id,
        action="update", patient_id=patient_id,
    )
    response.headers["ETag"] = f'"{updated.version}"'
    return _to_document_out(updated)


def _parse_etag(if_match: str) -> int:
    raw = if_match.strip().strip('"')
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(400, "If-Match must be a document version ETag.", title="Bad Request") from exc
