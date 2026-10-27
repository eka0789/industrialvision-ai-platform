"""Shift-scoped opaque refresh tokens with rotation and revocation.

- Tokens are cryptographically random 32-byte values (256-bit entropy).
- Stored in the database as SHA-256 hashes only; the plaintext is never kept.
- TTL: configured by `REFRESH_TOKEN_EXPIRE_HOURS` (default 12h, one factory shift).
- Rotation: every successful refresh revokes the used token and issues a new one.
- Revocation: logout and admin actions mark tokens as revoked immediately.

This complements the short-lived JWT access token (15 min default) so operators
stay authenticated across a shift without storing long-lived credentials client-side.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import RefreshToken, User


def _aware(dt: datetime) -> datetime:
    """Return a tz-aware UTC datetime, normalizing SQLite's naive values."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def issue_refresh_token(
    db: Session,
    user: User,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[str, RefreshToken]:
    """Create a new refresh token, store its hash, return the plaintext."""
    plain = secrets.token_urlsafe(32)  # 256 bits of entropy, URL-safe
    token_hash = _hash_token(plain)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.REFRESH_TOKEN_EXPIRE_HOURS)

    token_row = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        revoked=False,
        ip_address=ip_address,
        user_agent=user_agent[:400] if user_agent else None,
    )
    db.add(token_row)
    db.commit()
    db.refresh(token_row)
    return plain, token_row


def rotate_refresh_token(
    db: Session,
    plain_token: str,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[str, RefreshToken] | None:
    """Verify a plain token, revoke it, and issue a replacement.

    Returns (new_plain_token, new_row) on success; None if token is invalid,
    expired, revoked, or belongs to an inactive user.
    """
    token_hash = _hash_token(plain_token)
    token = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == token_hash)
        .first()
    )
    if token is None:
        return None
    if token.revoked:
        return None
    if _aware(token.expires_at) < datetime.now(timezone.utc):
        return None
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        return None

    # Revoke the used token
    token.revoked = True
    db.add(token)

    # Issue new one
    new_plain, new_row = issue_refresh_token(db, user, ip_address, user_agent)
    return new_plain, new_row


def revoke_all_user_tokens(db: Session, user_id: int) -> int:
    """Mark all active tokens for a user as revoked. Returns count."""
    updated = (
        db.query(RefreshToken)
        .filter(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
        .update({"revoked": True}, synchronize_session=False)
    )
    db.commit()
    return updated


def validate_refresh_token(db: Session, plain_token: str) -> RefreshToken | None:
    """Return the RefreshToken row if valid; otherwise None."""
    token_hash = _hash_token(plain_token)
    token = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == token_hash)
        .first()
    )
    if token is None or token.revoked:
        return None
    if _aware(token.expires_at) < datetime.now(timezone.utc):
        return None
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        return None
    return token