"""JWT utilities (HS256 sign/verify)."""

from __future__ import annotations

import time

import jwt as _jwt

from app.core.config import settings

ALGORITHM = settings.JWT_ALGORITHM


def create_access_token(user_id: int, role: str, expires_minutes: int | None = None) -> str:
    lifetime = expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    payload = {
        "sub": str(user_id),
        "role": role,
        "user_id": user_id,
        "exp": int(time.time()) + lifetime * 60,
        "typ": "JWT",
    }
    return _jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return _jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
