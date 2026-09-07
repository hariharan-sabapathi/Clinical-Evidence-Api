from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC

import jwt

from app.core.config import Settings

ACCESS = "access"
REFRESH = "refresh"


@dataclass(frozen=True)
class TokenPayload:
    sub: str
    role: str
    type: str
    jti: str
    exp: datetime


def _encode(settings: Settings, sub: str, role: str, token_type: str, ttl_seconds: int) -> tuple[str, str]:
    jti = str(uuid.uuid4())
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "role": role,
        "type": token_type,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti


def create_access_token(settings: Settings, sub: str, role: str) -> str:
    token, _ = _encode(settings, sub, role, ACCESS, settings.access_token_ttl_seconds)
    return token


def create_refresh_token(settings: Settings, sub: str, role: str) -> tuple[str, str]:
    """Returns (token, jti). Callers persist a hash of the token (never the
    token itself) in refresh_tokens so a leaked database dump can't be
    replayed as a live credential."""
    return _encode(settings, sub, role, REFRESH, settings.refresh_token_ttl_seconds)


def decode_token(settings: Settings, token: str) -> TokenPayload:
    data = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    return TokenPayload(
        sub=data["sub"],
        role=data["role"],
        type=data["type"],
        jti=data["jti"],
        exp=datetime.fromtimestamp(data["exp"], tz=UTC),
    )


def hash_token(token: str) -> str:
    """Refresh tokens are bearer-equivalent to a password, so the table only
    ever stores a SHA-256 digest (fixed-size, not user-tunable like bcrypt's
    cost factor -- this doesn't need to be slow, it needs to be a stable
    lookup key over a value that's already 128+ bits of entropy)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_opaque_secret() -> str:
    return secrets.token_urlsafe(32)
