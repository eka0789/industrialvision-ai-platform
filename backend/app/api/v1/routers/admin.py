"""Organization-scoped user administration; initial admin is provisioned via CLI.

All endpoints operate on the caller's organization only (A6): new users join
the caller's org, and listings never show members of another org. Cross-tenant
usernames are still globally unique (DB constraint) for login simplicity.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.core.constants import Role as RoleNames
from app.db.models import AuditLog, Role, User
from app.db.session import get_db
from app.services.tenant import get_user_org_id, scoped_query, stamp_org

router = APIRouter(prefix="/admin", tags=["admin"])


def _audit(db: Session, actor: User, action: str, details: dict, ip: str | None) -> None:
    db.add(
        AuditLog(
            user_id=actor.id,
            org_id=get_user_org_id(actor),
            action=action,
            details_json=details,
            ip_address=ip,
        )
    )


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    email: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(default=RoleNames.OPERATOR, max_length=40)


class AdminUserOut(BaseModel):
    id: int
    username: str
    email: str | None
    role: str
    is_active: bool
    org_id: int


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_roles(*RoleNames.admin_roles())),
) -> list[dict]:
    """List members of the caller's organization only."""
    users = scoped_query(db, User, _admin).all()
    return [
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role.name,
            "is_active": user.is_active,
            "org_id": user.org_id,
        }
        for user in users
    ]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreate,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_roles(*RoleNames.admin_roles())),
) -> dict:
    """Invite a user into the caller's organization (org-scoped invitation)."""
    if payload.role not in RoleNames.all():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown role")
    # Only owners may mint another owner/admin — admins stop at admins.
    if payload.role in RoleNames.admin_roles() and _admin.role.name != RoleNames.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the organization owner can create owner/admin accounts",
        )
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
    stamp_org(user, _admin)
    db.add(user)
    _audit(
        db,
        _admin,
        "admin.user.created",
        {"username": user.username, "role": role.name, "org_id": user.org_id},
        request.client.host if request.client else None,
    )
    db.commit()
    db.refresh(user)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": role.name,
        "is_active": user.is_active,
        "org_id": user.org_id,
    }


@router.patch("/users/{user_id}/status", status_code=status.HTTP_200_OK)
def set_user_active(
    user_id: int,
    is_active: bool,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_roles(*RoleNames.admin_roles())),
) -> dict:
    """Activate/deactivate a member of the caller's organization."""
    target = scoped_query(db, User, _admin).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.id == _admin.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot change your own status")
    target.is_active = is_active
    _audit(
        db,
        _admin,
        "admin.user.status_changed",
        {"target_user_id": target.id, "is_active": is_active},
        request.client.host if request.client else None,
    )
    db.commit()
    db.refresh(target)
    return {
        "id": target.id,
        "username": target.username,
        "role": target.role.name,
        "is_active": target.is_active,
        "org_id": get_user_org_id(target),
    }
