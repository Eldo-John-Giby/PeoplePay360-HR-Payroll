"""Password hashing + JWT issuing/decoding.

- Hashing uses bcrypt directly (no passlib — passlib 1.7.4 is unmaintained
  and breaks with bcrypt>=4.1).
- Tokens are signed with PyJWT (HS256). Access tokens carry the user id and
  a `type: access` claim; refresh tokens carry `type: refresh` and a longer
  expiry. `get_current_user` only accepts access tokens.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain_password: str) -> str:
    """Hash a password with bcrypt (12 rounds)."""
    return bcrypt.hashpw(
        plain_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time comparison of a candidate against a stored hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def _create_token(
    subject: int, token_type: str, expires_delta: timedelta, extra: dict[str, Any] | None = None
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: int, expires_delta: timedelta | None = None) -> str:
    delta = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _create_token(user_id, "access", delta)


def create_refresh_token(user_id: int, expires_delta: timedelta | None = None) -> str:
    delta = expires_delta or timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
    return _create_token(user_id, "refresh", delta)


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """Decode + validate a JWT.

    Raises `jwt.PyJWTError` subclasses (ExpiredSignatureError, InvalidTokenError)
    on any failure — callers translate those into 401 responses.
    """
    payload = jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(
            f"Expected a {expected_type!r} token, got {payload.get('type')!r}"
        )
    return payload