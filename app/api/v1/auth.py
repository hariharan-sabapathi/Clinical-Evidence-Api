from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session_unscoped, get_settings_dep
from app.core.config import Settings
from app.core.errors import ApiError
from app.models.user import RefreshToken, User
from app.schemas.auth import RefreshRequest, TokenResponse
from app.security.jwt import REFRESH, create_access_token, create_refresh_token, decode_token, hash_token
from app.security.passwords import verify_password

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="OAuth2 password grant",
    responses={401: {"description": "Invalid credentials"}},
)
async def issue_token(
    form: OAuth2PasswordRequestForm = Depends(),
    settings: Settings = Depends(get_settings_dep),
    session: AsyncSession = Depends(db_session_unscoped),
) -> TokenResponse:
    result = await session.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(form.password, user.hashed_password):
        raise ApiError(401, "Incorrect email or password.", title="Unauthorized")

    access = create_access_token(settings, str(user.id), user.role)
    refresh, jti = create_refresh_token(settings, str(user.id), user.role)
    session.add(
        RefreshToken(
            id=uuid.UUID(jti),
            user_id=user.id,
            token_hash=hash_token(refresh),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=settings.access_token_ttl_seconds)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate a refresh token for a new access/refresh pair",
    responses={401: {"description": "Invalid, expired, or already-used refresh token"}},
)
async def refresh_token(
    body: RefreshRequest,
    settings: Settings = Depends(get_settings_dep),
    session: AsyncSession = Depends(db_session_unscoped),
) -> TokenResponse:
    try:
        payload = decode_token(settings, body.refresh_token)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(401, "Invalid refresh token.", title="Unauthorized") from exc
    if payload.type != REFRESH:
        raise ApiError(401, "Not a refresh token.", title="Unauthorized")

    token_hash = hash_token(body.refresh_token)
    result = await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if stored is None or stored.revoked_at is not None or stored.expires_at < now:
        raise ApiError(401, "Refresh token is invalid, expired, or already used.", title="Unauthorized")

    # Rotate: the presented token is single-use. Revoking it here means a
    # replayed (stolen) refresh token only works once before this branch
    # starts rejecting both the old and new session, which is the detection
    # signal for token theft.
    stored.revoked_at = now

    access = create_access_token(settings, payload.sub, payload.role)
    new_refresh, new_jti = create_refresh_token(settings, payload.sub, payload.role)
    session.add(
        RefreshToken(
            id=uuid.UUID(new_jti),
            user_id=uuid.UUID(payload.sub),
            token_hash=hash_token(new_refresh),
            expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    return TokenResponse(
        access_token=access, refresh_token=new_refresh, expires_in=settings.access_token_ttl_seconds
    )
