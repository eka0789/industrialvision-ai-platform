"""Authentication endpoints with per-account lockout and audit logging."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user
from app.core.config import settings
from app.core.jwt import create_access_token
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import LoginResponse, Token, TokenRefreshRequest, TokenRefreshResponse, TokenRequest, UserRead
from app.services.auth_security import (
    clear_failure_window,
    is_account_locked,
    record_login_attempt,
    record_logout,
)
from app.services.refresh_tokens import issue_refresh_token, revoke_all_user_tokens, rotate_refresh_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    payload: TokenRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResponse:
    username = payload.username
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    # Account lockout check (covers users that exist)
    if is_account_locked(db, username):
        # Write the blocked attempt as an audit event
        record_login_attempt(
            db, user=None, username=username, success=False,
            ip_address=ip, user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked due to repeated failures",
            headers={"Retry-After": str(settings.LOGIN_LOCKOUT_MINUTES * 60)},
        )

    user = db.query(User).filter(User.username == username).first()

    if user is None or not user.check_password(payload.password):
        # Failed attempt (user may or may not exist)
        record_login_attempt(
            db, user=None, username=username, success=False,
            ip_address=ip, user_agent=user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        record_login_attempt(
            db, user=user, username=username, success=False,
            ip_address=ip, user_agent=user_agent,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    # Successful login: clear the failure window and log
    clear_failure_window(db, username)
    record_login_attempt(
        db, user=user, username=username, success=True,
        ip_address=ip, user_agent=user_agent,
    )

    access_token = create_access_token(user.id, user.role.name)
    refresh_plain, _ = issue_refresh_token(db, user, ip, user_agent)
    return LoginResponse(
        token=Token(
            access_token=access_token,
            token_type="bearer",
            role=user.role.name,
            user_id=user.id,
        ),
        user=UserRead(
            id=user.id,
            username=user.username,
            role=user.role.name,
            is_active=user.is_active,
        ),
        refresh_token=refresh_plain,
    )


@router.post("/refresh", response_model=TokenRefreshResponse)
def refresh(
    payload: TokenRefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenRefreshResponse:
    """Exchange a valid shift-scoped refresh token for a new access token and a rotated refresh token."""
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    rotated = rotate_refresh_token(db, payload.refresh_token, ip, user_agent)
    if rotated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or revoked refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    new_refresh_plain, refresh_row = rotated
    user = db.get(User, refresh_row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    new_access_token = create_access_token(user.id, user.role.name)
    return TokenRefreshResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_plain,
        token_type="bearer",
        role=user.role.name,
        user_id=user.id,
    )


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> UserRead:
    return UserRead(id=user.id, username=user.username, role=user.role.name, is_active=user.is_active)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke all active refresh tokens for the user and record audit log."""
    ip = request.client.host if request.client else None
    revoke_all_user_tokens(db, user.id)
    record_logout(db, user.id, ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
