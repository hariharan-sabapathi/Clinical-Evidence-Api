"""Shared FastAPI dependencies. ``current_actor`` decodes the bearer JWT;
``db_session`` opens exactly one transaction per request, scoped to that
actor via ``SET LOCAL`` (see app/db/session.py), so every RLS-protected
query in the route function runs inside a transaction the database already
knows the caller's identity for -- there's no window where a query could
run before the actor is set.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import cast

import jwt as pyjwt
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.db.session import Database
from app.observability.logging import actor_id_var
from app.security.jwt import ACCESS, decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token", auto_error=False)


@dataclass(frozen=True)
class Actor:
    id: str
    role: str


def get_db(request: Request) -> Database:
    # request.app.state is a dynamically-typed Starlette State object --
    # the cast is a type-checker aid, not a runtime check (the attribute is
    # set once, unconditionally, in app/main.py's lifespan).
    return cast(Database, request.app.state.db)


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_settings_dep() -> Settings:
    return get_settings()


async def current_actor(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings_dep),
) -> Actor:
    if not token:
        raise ApiError(401, "Missing bearer token.", title="Unauthorized")
    try:
        payload = decode_token(settings, token)
    except pyjwt.ExpiredSignatureError as exc:
        raise ApiError(401, "Token has expired.", title="Unauthorized") from exc
    except pyjwt.InvalidTokenError as exc:
        raise ApiError(401, "Invalid token.", title="Unauthorized") from exc
    if payload.type != ACCESS:
        raise ApiError(401, "Not an access token.", title="Unauthorized")
    request.state.actor_id = payload.sub
    actor_id_var.set(payload.sub)
    return Actor(id=payload.sub, role=payload.role)


def require_roles(*roles: str) -> Callable[..., Coroutine[None, None, Actor]]:
    async def _check(actor: Actor = Depends(current_actor)) -> Actor:
        if actor.role not in roles:
            raise ApiError(
                403,
                "The current role is not permitted to access this resource.",
                title="Forbidden",
            )
        return actor

    return _check


async def db_session_for_actor(
    actor: Actor = Depends(current_actor), db: Database = Depends(get_db)
) -> AsyncIterator[AsyncSession]:
    async with db.session_as(actor.id, actor.role) as session:
        yield session


async def db_session_unscoped(db: Database = Depends(get_db)) -> AsyncIterator[AsyncSession]:
    async with db.session_unscoped() as session:
        yield session
