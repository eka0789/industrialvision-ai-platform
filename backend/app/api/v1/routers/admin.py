"""Admin endpoints for seed data and system management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, get_current_user
from app.core.security import verify_password
from app.db.models import User, Role
from app.db.session import SessionLocal
from app.schemas.common import Message
from app.schemas.user import UserCreate

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/seed", response_model=Message, status_code=status.HTTP_201_OK)
def seed_initial_data(db: Session = Depends(get_db)):
    """Populate initial seed data: admin user and default roles."""
    # Create default roles if not exist
    admin_role = db.query(Role).filter(Role.name == "admin").first()
    if not admin_role:
        admin_role = Role(name="admin", description="Administrator role")
        db.add(admin_role)
        db.commit()

    operator_role = db.query(Role).filter(Role.name == "operator").first()
    if not operator_role:
        operator_role = Role(name="operator", description="Operator role")
        db.add(operator_role)
        db.commit()

    # Create admin user if not exist
    admin_user = db.query(User).filter(User.username == "admin").first()
    if not admin_user:
        admin_user = User(
            username="admin",
            email="admin@industrialvision.ai",
            hashed_password=verify_password("admin123"),
            role_id=admin_role.id,
            is_active=True,
        )
        db.add(admin_user)
        db.commit()

    return {"detail": "Seed data populated successfully"}