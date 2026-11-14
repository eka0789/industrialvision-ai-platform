"""FastAPI dependencies: current user, RBAC, database."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session


from app.db.session import get_db
from app.db.models import User  # Fixed import path

security = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the caller from the Authorization header, or (cookie mode) the
    httpOnly access cookie. The header always wins so API clients are
    unaffected by the SPA cookie mode."""
    token: str | None = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    else:
        from app.services.session_cookies import bearer_token_from_request

        token = bearer_token_from_request(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid bearer token")
    try:
        from app.core.jwt import decode_token

        payload = decode_token(token)
        user_id = int(payload.get("user_id", 0))
        if user_id <= 0 or payload.get("sub") != str(user_id):
            raise ValueError("Invalid token subject")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_roles(*allowed: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role.name not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role.name}' is not allowed. Required: {', '.join(allowed)}",
            )
        return user

    return checker


def require_any_role(*roles: str):
    """Allow any of the listed roles (used for permissively protected routes)."""
    return require_roles(*roles)
