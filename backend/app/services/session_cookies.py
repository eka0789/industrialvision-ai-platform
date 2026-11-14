"""httpOnly-cookie session mode (AUTH_TOKEN_STORAGE=cookie) + CSRF defense.

Two token-storage modes share one auth backend:

- ``body`` (default): tokens are returned in JSON bodies exactly as before —
  the mode for API clients and the current SPA behavior.
- ``cookie``: access and refresh tokens are set as httpOnly cookies and are
  NEVER returned in bodies (the fields are empty strings). The SPA authenticates
  by sending cookies; because browsers attach cookies automatically, every
  unsafe (POST/PUT/PATCH/DELETE) request must also echo the double-submit CSRF
  token: the non-httpOnly ``iv_csrf`` cookie value in the ``X-CSRF-Token``
  header. A cross-site attacker can neither read the CSRF cookie (same-origin
  policy) nor make the browser send matching headers, so forged requests fail.

Cookie attributes: ``Secure`` whenever the deployment is production or
``AUTH_COOKIE_SECURE`` is set; ``SameSite=Lax`` for the access cookie (top-level
navigation keeps working), ``SameSite=Strict`` scoped to ``/auth/refresh`` for
the refresh cookie so it is only ever attached to the silent-refresh call.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, Response, status

from app.core.config import settings

ACCESS_COOKIE = "iv_access"
REFRESH_COOKIE = "iv_refresh"
CSRF_COOKIE = "iv_csrf"
CSRF_HEADER = "X-CSRF-Token"

# Endpoints that establish or rotate the session run before a CSRF pair
# exists; they are protected by SameSite cookies instead.
CSRF_EXEMPT_PATHS = {"/", "/health", "/metrics", "/auth/login", "/auth/refresh"}


def _cookie_secure() -> bool:
    if settings.AUTH_COOKIE_SECURE is not None:
        return settings.AUTH_COOKIE_SECURE
    return settings.APP_ENV.casefold() == "production"


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Attach access + refresh + CSRF cookies for cookie mode."""
    secure = _cookie_secure()
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_HOURS * 3600,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/auth/refresh",
    )
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        max_age=settings.REFRESH_TOKEN_EXPIRE_HOURS * 3600,
        httponly=False,  # the SPA must read it to echo the double-submit header
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    """Delete all session cookies (used by cookie-mode logout)."""
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/auth/refresh"), (CSRF_COOKIE, "/")):
        response.delete_cookie(name, path=path)


def cookie_mode() -> bool:
    return settings.AUTH_TOKEN_STORAGE == "cookie"


def bearer_token_from_request(request: Request) -> str | None:
    """Access token from the cookie (cookie mode) — header wins if present."""
    if not cookie_mode():
        return None
    return request.cookies.get(ACCESS_COOKIE)


def refresh_token_from_request(request: Request) -> str | None:
    """Refresh token from the path-scoped cookie (cookie mode)."""
    if not cookie_mode():
        return None
    return request.cookies.get(REFRESH_COOKIE)


def verify_csrf(request: Request) -> None:
    """Double-submit check: header must equal the CSRF cookie value.

    Called by the CSRF middleware for unsafe methods in cookie mode. Fails
    closed: missing cookie, missing header, or mismatch are all rejected.
    """
    cookie_value = request.cookies.get(CSRF_COOKIE)
    header_value = request.headers.get(CSRF_HEADER)
    if not cookie_value or not header_value or not secrets.compare_digest(cookie_value, header_value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed: missing or mismatching X-CSRF-Token header",
        )
