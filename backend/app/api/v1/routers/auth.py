"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, get_current_user
from app.core.constants import Role
from app.core.jwt import create_access_token
from app.core.security import hash_password
from app.schemas.user import UserCreate
from app.schemas.common import Message
from app.schemas.auth import LoginResponse, Token, UserRead
from app.db.session import get_db
from app.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
def login(payload: UserCreate, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not user.check_password(payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")
    token = create_access_token(user.id, user.role.name)
    return {"token": Token(access_token=token, token_type="bearer", role=user.role.name, user_id=user.id), "user": UserRead(id=user.id, username=user.username, role=user.role.name, is_active=user.is_active)}


@router.post("/logout")
def logout():
    return {"detail": "Logged out (stateless JWT)"}


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)):
    return UserRead(id=user.id, username=user.username, role=user.role.name, is_active=user.is_active)
