"""Idempotency-Key handling for POST endpoints that create work.

Storage is (key, request_body_hash, response_status, response_body) with a
TTL. Replay with the same key and the same body hash returns the stored
response verbatim without re-running the handler. Replay with the same key
and a *different* body hash is a 422 -- the client is asserting "this is
the same logical request" while sending a different payload, which is
either a bug or a key collision, and silently picking one interpretation
(run it again? ignore the new body?) would hide that from them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models.idempotency import IdempotencyKey


def hash_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class IdempotentReplay:
    status_code: int
    body: dict[str, Any]


async def check_and_reserve(
    session: AsyncSession, key: str, body: bytes, ttl_seconds: int
) -> IdempotentReplay | None:
    """Returns a stored response if this key was already used with an
    identical body (a true replay). Raises 422 if the key was already used
    with a *different* body. Returns None if this is a new key -- the
    caller runs the handler and calls ``store_response`` afterward."""
    body_hash = hash_body(body)
    existing = await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
    row = existing.scalar_one_or_none()
    if row is None:
        return None

    now = datetime.now(UTC)
    if row.expires_at < now:
        await session.delete(row)
        return None

    if row.request_body_hash != body_hash:
        raise ApiError(
            422,
            "Idempotency-Key was already used with a different request body.",
            title="Idempotency Key Reused",
        )
    return IdempotentReplay(status_code=row.response_status, body=row.response_body)


async def store_response(
    session: AsyncSession, key: str, body: bytes, status_code: int, response_body: dict[str, Any], ttl_seconds: int
) -> None:
    now = datetime.now(UTC)
    stmt = (
        insert(IdempotencyKey)
        .values(
            key=key,
            request_body_hash=hash_body(body),
            response_status=status_code,
            response_body=response_body,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )
    await session.execute(stmt)
