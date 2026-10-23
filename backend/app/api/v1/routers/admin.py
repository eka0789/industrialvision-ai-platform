"""Administrator-only user administration; initial admin is provisioned via CLI."""

from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.core.constants import Role as RoleNames
from app.db.models import Role, User
from app.db.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(default=RoleNames.OPERATOR, max_length=40)


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_roles(RoleNames.ADMIN)),
) -> dict:
    if payload.role not in RoleNames.all():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown role")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    if payload.email and db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    role = db.query(Role).filter(Role.name == payload.role).first()
    if role is None:
        role = Role(name=payload.role, description=f"{payload.role.replace('_', ' ').title()} role")
        db.add(role)
        db.flush()

    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password="",
        role_id=role.id,
        is_active=True,
    )
    user.set_password(payload.password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": role.name,
        "is_active": user.is_active,
    }
