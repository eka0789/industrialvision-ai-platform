"""SKU Inspection Recipe & Multi-Zone ROI Management API (Fase 1 & Enhancement 2).

RBAC:
- Read (GET): owner, admin, ai_engineer, operator, viewer
- Create / Update / Delete (POST, PATCH, PUT, DELETE): owner, admin, ai_engineer
- Activate / Changeover (POST /{id}/activate, POST /changeover): owner, admin, ai_engineer, operator
Enforces atomic activation: at most 1 active InspectionRecipe per camera_id at any time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.core.constants import Role as RoleNames
from app.db.models import AuditLog, Camera, InspectionRecipe, ModelVersion, ProductSKU, ProductionLine, User
from app.db.session import get_db
from app.services.tenant import get_user_org_id, scoped_one, scoped_query, stamp_org
from app.vision.preprocessing import PreprocessingConfig
from app.vision.roi_pipeline import ROIZoneSpec, default_food_zones

router = APIRouter(prefix="/recipes", tags=["sku-recipes"])

READ_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.AI_ENGINEER, RoleNames.OPERATOR, RoleNames.VIEWER)
WRITE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.AI_ENGINEER)
ACTIVATE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.AI_ENGINEER, RoleNames.OPERATOR)


class RecipeCreate(BaseModel):
    sku_id: int | None = Field(default=None, gt=0)
    sku_code: str | None = Field(default=None, min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    line_id: int | None = Field(default=None, gt=0)
    camera_id: int | None = Field(default=None, gt=0)
    model_version_id: int | None = Field(default=None, gt=0)
    is_active: bool = False
    pass_sample_rate: int = Field(default=500, ge=1, le=100000)
    preprocessing_config: dict[str, Any] | None = None
    preprocessing: dict[str, Any] | None = None
    roi_zones: list[ROIZoneSpec] | None = None
    zones: list[ROIZoneSpec] | None = None


class RecipeUpdate(BaseModel):
    sku_id: int | None = Field(default=None, gt=0)
    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    line_id: int | None = Field(default=None, gt=0)
    camera_id: int | None = Field(default=None, gt=0)
    model_version_id: int | None = Field(default=None, gt=0)
    pass_sample_rate: int | None = Field(default=None, ge=1, le=100000)
    preprocessing_config: dict[str, Any] | None = None
    preprocessing: dict[str, Any] | None = None
    roi_zones: list[ROIZoneSpec] | None = None
    zones: list[ROIZoneSpec] | None = None


class ChangeoverRequest(BaseModel):
    sku_code: str | None = Field(default=None, max_length=80)
    recipe_id: int | None = Field(default=None, gt=0)
    camera_id: int | None = Field(default=None, gt=0)
    source: str = Field(default="operator_ui", max_length=60)


class RecipeOut(BaseModel):
    id: int
    org_id: int
    sku_id: int | None
    sku_code: str | None
    name: str
    description: str | None
    line_id: int | None
    camera_id: int | None
    model_version_id: int | None
    is_active: bool
    pass_sample_rate: int
    preprocessing_config: dict[str, Any]
    preprocessing_json: dict[str, Any]
    roi_zones: list[dict[str, Any]]
    zones_json: list[dict[str, Any]]
    created_at: datetime


def _serialize_recipe(row: InspectionRecipe) -> dict[str, Any]:
    prep = row.preprocessing_config or PreprocessingConfig().to_dict()
    zones = row.roi_zones if row.roi_zones is not None else default_food_zones()
    sku_code = row.sku_code or (row.sku.sku_code if row.sku else None)
    return {
        "id": row.id,
        "org_id": row.org_id,
        "sku_id": row.sku_id,
        "sku_code": sku_code,
        "name": row.name,
        "description": row.description,
        "line_id": row.line_id,
        "camera_id": row.camera_id,
        "model_version_id": row.model_version_id,
        "is_active": row.is_active,
        "pass_sample_rate": row.pass_sample_rate,
        "preprocessing_config": prep,
        "preprocessing_json": prep,
        "roi_zones": zones,
        "zones_json": zones,
        "created_at": row.created_at,
    }


def _recipe_or_404(db: Session, user: User, recipe_id: int) -> InspectionRecipe:
    row = scoped_one(db, InspectionRecipe, user, recipe_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SKU recipe not found")
    return row


def _deactivate_camera_peers(db: Session, user: User, camera_id: int | None, line_id: int | None, exclude_id: int | None = None) -> None:
    """Enforce atomic 1-active-recipe-per-camera rule within the organization."""
    peers = scoped_query(db, InspectionRecipe, user).filter(InspectionRecipe.is_active.is_(True))
    if exclude_id is not None:
        peers = peers.filter(InspectionRecipe.id != exclude_id)
    if camera_id is not None:
        peers = peers.filter(InspectionRecipe.camera_id == camera_id)
    elif line_id is not None:
        peers = peers.filter(InspectionRecipe.line_id == line_id)
    else:
        return
    for peer in peers.all():
        peer.is_active = False


@router.get("", response_model=list[RecipeOut])
def list_recipes(
    camera_id: int | None = Query(default=None, gt=0),
    line_id: int | None = Query(default=None, gt=0),
    sku_id: int | None = Query(default=None, gt=0),
    active_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict[str, Any]]:
    query = scoped_query(db, InspectionRecipe, user)
    if camera_id is not None:
        query = query.filter(InspectionRecipe.camera_id == camera_id)
    if line_id is not None:
        query = query.filter(InspectionRecipe.line_id == line_id)
    if sku_id is not None:
        query = query.filter(InspectionRecipe.sku_id == sku_id)
    if active_only:
        query = query.filter(InspectionRecipe.is_active.is_(True))
    rows = query.order_by(InspectionRecipe.is_active.desc(), InspectionRecipe.id.asc()).all()
    return [_serialize_recipe(r) for r in rows]


@router.post("", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
def create_recipe(
    payload: RecipeCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, Any]:
    if payload.camera_id and not scoped_one(db, Camera, user, payload.camera_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    if payload.line_id and not scoped_one(db, ProductionLine, user, payload.line_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Production line not found")
    if payload.model_version_id and not scoped_one(db, ModelVersion, user, payload.model_version_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model version not found")

    sku_row: ProductSKU | None = None
    if payload.sku_id is not None:
        sku_row = scoped_one(db, ProductSKU, user, payload.sku_id)
        if sku_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ProductSKU not found")
    elif payload.sku_code:
        sku_row = scoped_query(db, ProductSKU, user).filter(ProductSKU.sku_code == payload.sku_code).first()
        if sku_row is None:
            sku_row = ProductSKU(
                sku_code=payload.sku_code,
                name=payload.name,
                category="snack",
                packaging_type="flow_wrap",
                is_active=True,
            )
            stamp_org(sku_row, user)
            db.add(sku_row)
            db.flush()

    resolved_sku_code = payload.sku_code or (sku_row.sku_code if sku_row else f"SKU-{int(datetime.now().timestamp())}")
    raw_prep = payload.preprocessing_config if payload.preprocessing_config is not None else payload.preprocessing
    prep_cfg = PreprocessingConfig.from_dict(raw_prep).to_dict()
    if raw_prep:
        prep_cfg.update({k: v for k, v in raw_prep.items() if k not in prep_cfg})

    raw_zones = payload.roi_zones if payload.roi_zones is not None else payload.zones
    zones_list = [z.model_dump() for z in raw_zones] if raw_zones is not None else default_food_zones()

    if payload.is_active:
        _deactivate_camera_peers(db, user, payload.camera_id, payload.line_id)

    row = InspectionRecipe(
        sku_id=sku_row.id if sku_row else None,
        sku_code=resolved_sku_code,
        name=payload.name,
        description=payload.description,
        line_id=payload.line_id,
        camera_id=payload.camera_id,
        model_version_id=payload.model_version_id,
        is_active=payload.is_active,
        pass_sample_rate=payload.pass_sample_rate,
        preprocessing_config=prep_cfg,
        roi_zones=zones_list,
    )
    stamp_org(row, user)
    db.add(row)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="recipe.created",
            details_json={"sku_id": row.sku_id, "sku_code": row.sku_code, "zones_count": len(zones_list)},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_recipe(row)


@router.get("/{recipe_id}", response_model=RecipeOut)
def get_recipe(
    recipe_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> dict[str, Any]:
    return _serialize_recipe(_recipe_or_404(db, user, recipe_id))


@router.patch("/{recipe_id}", response_model=RecipeOut)
@router.put("/{recipe_id}", response_model=RecipeOut)
def update_recipe(
    recipe_id: int,
    payload: RecipeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict[str, Any]:
    row = _recipe_or_404(db, user, recipe_id)
    if payload.sku_id is not None:
        sku_row = scoped_one(db, ProductSKU, user, payload.sku_id)
        if sku_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ProductSKU not found")
        row.sku_id = sku_row.id
        row.sku_code = sku_row.sku_code
    if payload.name is not None:
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    if payload.line_id is not None:
        row.line_id = payload.line_id
    if payload.camera_id is not None:
        row.camera_id = payload.camera_id
    if payload.model_version_id is not None:
        row.model_version_id = payload.model_version_id
    if payload.pass_sample_rate is not None:
        row.pass_sample_rate = payload.pass_sample_rate

    raw_prep = payload.preprocessing_config if payload.preprocessing_config is not None else payload.preprocessing
    if raw_prep is not None:
        merged = dict(row.preprocessing_config or {})
        merged.update(raw_prep)
        cfg = PreprocessingConfig.from_dict(merged).to_dict()
        cfg.update(raw_prep)
        row.preprocessing_config = cfg

    raw_zones = payload.roi_zones if payload.roi_zones is not None else payload.zones
    if raw_zones is not None:
        row.roi_zones = [z.model_dump() for z in raw_zones]

    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="recipe.updated",
            details_json={"recipe_id": row.id, "sku_code": row.sku_code},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_recipe(row)


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recipe(
    recipe_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> Response:
    row = _recipe_or_404(db, user, recipe_id)
    db.delete(row)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="recipe.deleted",
            details_json={"recipe_id": recipe_id, "sku_code": row.sku_code},
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{recipe_id}/activate", response_model=RecipeOut)
def activate_recipe(
    recipe_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ACTIVATE_ROLES)),
) -> dict[str, Any]:
    """Activate a SKU recipe for changeover (atomically deactivates peer recipes on the same camera)."""
    row = _recipe_or_404(db, user, recipe_id)
    _deactivate_camera_peers(db, user, row.camera_id, row.line_id, exclude_id=row.id)
    row.is_active = True
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="recipe.activated",
            details_json={
                "recipe_id": row.id,
                "sku_id": row.sku_id,
                "sku_code": row.sku_code,
                "camera_id": row.camera_id,
            },
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(row)
    return _serialize_recipe(row)


@router.post("/changeover", response_model=RecipeOut)
def plc_sku_changeover(
    payload: ChangeoverRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ACTIVATE_ROLES)),
) -> dict[str, Any]:
    """Receive SKU changeover code from PLC/MES or Operator UI and switch active recipe."""
    query = scoped_query(db, InspectionRecipe, user)
    if payload.recipe_id is not None:
        row = query.filter(InspectionRecipe.id == payload.recipe_id).first()
    elif payload.sku_code:
        row = query.filter(InspectionRecipe.sku_code == payload.sku_code).first()
        if row is None:
            sku_obj = scoped_query(db, ProductSKU, user).filter(ProductSKU.sku_code == payload.sku_code).first()
            if sku_obj is not None:
                row = query.filter(InspectionRecipe.sku_id == sku_obj.id).first()
    else:
        row = None

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matching SKU recipe not found")
    if payload.camera_id is not None:
        row.camera_id = payload.camera_id
    return activate_recipe(row.id, request, db, user)
