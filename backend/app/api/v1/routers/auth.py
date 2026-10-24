"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user
from app.core.jwt import create_access_token
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import LoginResponse, Token, TokenRequest, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: TokenRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not user.check_password(payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    token = create_access_token(user.id, user.role.name)
    return LoginResponse(
        token=Token(
            access_token=token,
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
    )


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> UserRead:
    return UserRead(id=user.id, username=user.username, role=user.role.name, is_active=user.is_active)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout():
    """JWT access tokens are stateless; clients discard the token on logout."""
    pass
