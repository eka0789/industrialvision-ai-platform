"""Account-level login lockout and audit logging.

Tracks per-account consecutive failures within a rolling window and blocks
login while the account is locked.  This complements the per-IP budget at
the Nginx edge so that:
- Distributing guesses across many IPs still triggers an account lock.
- A single operator's fat-finger typos don't cause a lockout instantly.

The failure window slides: only failures *within* LOCKOUT_MINUTES are counted.
A single successful login flushes the failure window for that account.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AuditLog, LoginAttempt, User


def _client_ip(request_headers: dict[str, str]) -> str | None:
    """Extract the originating IP, honouring X-Forwarded-For only when configured."""
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request_headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request_headers.get("x-real-ip") or None


def is_account_locked(db: Session, username: str) -> bool:
    """True when the account has exceeded the failure budget in the lockout window."""
    window = datetime.now(timezone.utc) - timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
    failures = (
        db.query(LoginAttempt)
        .filter(
            LoginAttempt.username == username,
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at >= window,
        )
        .count()
    )
    return failures >= settings.LOGIN_MAX_FAILURES


def record_login_attempt(
    db: Session,
    *,
    user: User | None,
    username: str,
    success: bool,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    """Persist one login attempt row and an AuditLog row for the same event."""
    attempt = LoginAttempt(
        user_id=user.id if user else None,
        username=username,
        success=success,
        ip_address=ip_address,
        user_agent=user_agent[:400] if user_agent else None,
    )
    db.add(attempt)

    action = "auth.login_success" if success else "auth.login_failure"
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            details_json={"username": username, "ip": ip_address},
            ip_address=ip_address,
        )
    )
    db.commit()


def clear_failure_window(db: Session, username: str) -> None:
    """Remove all failure records for an account after a successful login.

    A single success resets the counter so one bad-day run of typos does
    not permanently increase the effective lockout sensitivity.
    """
    db.query(LoginAttempt).filter(
        LoginAttempt.username == username,
        LoginAttempt.success.is_(False),
    ).delete(synchronize_session=False)
    db.commit()


def record_logout(db: Session, user_id: int, ip_address: str | None) -> None:
    db.add(AuditLog(user_id=user_id, action="auth.logout", ip_address=ip_address))
    db.commit()
