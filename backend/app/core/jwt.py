"""JWT utilities (HS256 sign/verify)."""

from __future__ import annotations

import time

import jwt as _jwt

from app.config import settings

ALGORITHM = settings.JWT_ALGORITHM


def create_access_token(user_id: int, role: str, expires_minutes: int = 15) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "user_id": user_id,
        "exp": int(time.time()) + expires_minutes * 60,
        "typ": "JWT",
    }
    return _jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return _jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
