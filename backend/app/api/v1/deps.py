"""FastAPI dependencies: current user, RBAC, database."""

from __future__ import annotations

from typing import List

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.core.constants import Role
from app.db.session import get_db
from app.db.models import User  # Fixed import path

security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid bearer token")
    token = credentials.credentials
    try:
        from app.core.jwt import decode_token
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.get(User, payload["user_id"])
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
