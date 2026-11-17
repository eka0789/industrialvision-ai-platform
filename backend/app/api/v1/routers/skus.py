"""ProductSKU Master CRUD API (Fase 1 Food Division).

RBAC:
- Read (GET): owner, admin, ai_engineer, operator, viewer
- Write (POST, PATCH, PUT, DELETE): owner, admin, ai_engineer (operator is read-only for SKU master)
Multi-tenant: all queries and uniqueness constraints are scoped to ``org_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.core.constants import Role as RoleNames
from app.db.models import AuditLog, ProductSKU, User
from app.db.session import get_db
from app.services.tenant import get_user_org_id, scoped_one, scoped_query, stamp_org

router = APIRouter(prefix="/skus", tags=["product-skus"])

READ_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.AI_ENGINEER, RoleNames.OPERATOR, RoleNames.VIEWER)
WRITE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.AI_ENGINEER)

FoodCategory = Literal["bakery", "beverage", "dairy", "snack", "frozen", "condiment", "other"]


class ProductSKUCreate(BaseModel):
    sku_code: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=160)
    category: str = Field(default="snack", min_length=2, max_length=60)
    packaging_type: str = Field(default="flow_wrap", min_length=2, max_length=80)
    expiry_format: str | None = Field(
        default=r"(?:EXP|BB|LOT)?\s*\d{2}[/.\-]\d{2}[/.\-]\d{2,4}",
        max_length=160,
    )
    is_active: bool = True


class ProductSKUUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    category: str | None = Field(default=None, min_length=2, max_length=60)
    packaging_type: str | None = Field(default=None, min_length=2, max_length=80)
    expiry_format: str | None = Field(default=None, max_length=160)
    is_active: bool | None = None


class ProductSKUOut(BaseModel):
    id: int
    org_id: int
    sku_code: str
    name: str
    category: str
    packaging_type: str
    expiry_format: str | None
    is_active: bool
    created_at: datetime


def _serialize_sku(row: ProductSKU) -> dict[str, Any]:
    return {
        "id": row.id,
        "org_id": row.org_id,
        "sku_code": row.sku_code,
        "name": row.name,
        "category": row.category,
        "packaging_type": row.packaging_type,
        "expiry_format": row.expiry_format,
        "is_active": row.is_active,
        "created_at": row.created_at,
    }


def _sku_or_404(db: Session, user: User, sku_id: int) -> ProductSKU:
    row = scoped_one(db, ProductSKU, user, sku_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ProductSKU not found")
    return row


@router.get("", response_model=list[ProductSKUOut])
def list_skus(
    category: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict[str, Any]]:
    query = scoped_query(db, ProductSKU, user)
    if category:
        query = query.filter(ProductSKU.category == category)
    if active_only:
        query = query.filter(ProductSKU.is_active.is_(True))
    rows = query.order_by(ProductSKU.id.asc()).all()
    return [_serialize_sku(r) for r in rows]


@router.post("", response_model=ProductSKUOut, status_code=status.HTTP_201_CREATED)
def create_sku(
    payload: ProductSKUCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, Any]:
    if scoped_query(db, ProductSKU, user).filter(ProductSKU.sku_code == payload.sku_code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SKU code already exists in this organization")

    row = ProductSKU(
        sku_code=payload.sku_code,
        name=payload.name,
        category=payload.category,
        packaging_type=payload.packaging_type,
        expiry_format=payload.expiry_format,
        is_active=payload.is_active,
    )
    stamp_org(row, user)
    db.add(row)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="sku.created",
            details_json={"sku_code": row.sku_code, "category": row.category, "packaging_type": row.packaging_type},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_sku(row)


@router.get("/{sku_id}", response_model=ProductSKUOut)
def get_sku(
    sku_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> dict[str, Any]:
    return _serialize_sku(_sku_or_404(db, user, sku_id))


@router.patch("/{sku_id}", response_model=ProductSKUOut)
@router.put("/{sku_id}", response_model=ProductSKUOut)
def update_sku(
    sku_id: int,
    payload: ProductSKUUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, Any]:
    row = _sku_or_404(db, user, sku_id)
    for k, v in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(row, k, v)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="sku.updated",
            details_json={"sku_id": row.id, "sku_code": row.sku_code},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_sku(row)


@router.delete("/{sku_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sku(
    sku_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> Response:
    row = _sku_or_404(db, user, sku_id)
    db.delete(row)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="sku.deleted",
            details_json={"sku_id": sku_id, "sku_code": row.sku_code},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
