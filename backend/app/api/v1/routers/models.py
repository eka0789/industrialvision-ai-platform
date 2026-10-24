"""Model registry and metadata routes (T20).

Tracks framework, artifact reference/ID, task, classes, version, thresholds,
training-data provenance, license, checksum, and active status. Writes are
restricted to admin/AI-engineer roles; artifact references are resolved
server-side strictly under MODEL_ROOT — never taken as filesystem paths.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, require_roles
from app.core.constants import Role as RoleNames
from app.db.models import ModelEvaluation, ModelVersion, User
from app.db.session import get_db
from app.schemas.model import (
    SUPPORTED_FRAMEWORKS,
    SUPPORTED_TASKS,
    EvaluationIn,
    ModelVersionCreate,
    ModelVersionUpdate,
)
from app.services.model_registry import (
    ArtifactReferenceError,
    checksum_artifact,
    resolve_artifact_reference,
)

router = APIRouter(prefix="/models", tags=["models"])

_WRITE_ROLES = (RoleNames.ADMIN, RoleNames.AI_ENGINEER)


def _serialize(model: ModelVersion) -> dict[str, Any]:
    return {
        "id": model.id,
        "name": model.name,
        "description": model.description,
        "framework": model.framework,
        "framework_version": model.framework_version,
        "task_type": model.task_type,
        "artifact_id": model.artifact_id,
        "version": model.version,
        "license": model.license,
        "checksum": model.checksum,
        "classes": model.classes_json,
        "thresholds": model.thresholds_json,
        "provenance": model.provenance_json,
        "source_path": model.source_path,
        "device": model.device,
        "config_json": model.config_json,
        "is_active": model.is_active,
        "status": model.status,
    }


def _serialize_evaluation(row: ModelEvaluation) -> dict[str, Any]:
    return {
        "id": row.id,
        "model_version_id": row.model_version_id,
        "task_type": row.task_type,
        "metrics_json": row.metrics_json,
        "hardware": row.hardware,
        "input_size": row.input_size,
        "batch_size": row.batch_size,
        "measured_at": row.measured_at.isoformat() if row.measured_at else None,
    }


def _get_model(db: Session, model_id: int) -> ModelVersion:
    model = db.get(ModelVersion, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model version not found")
    return model


def _resolve(reference: str) -> str:
    """Resolve an artifact reference under MODEL_ROOT; return its server checksum."""
    try:
        path = resolve_artifact_reference(reference)
        return checksum_artifact(path)
    except ArtifactReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Artifact could not be read for checksumming",
        ) from exc


def _apply_active(db: Session, model: ModelVersion, active: bool) -> None:
    """Keep at most one active model per task type."""
    if active:
        db.query(ModelVersion).filter(
            ModelVersion.is_active.is_(True),
            ModelVersion.task_type == model.task_type,
            ModelVersion.id != model.id,
        ).update({"is_active": False}, synchronize_session=False)
    model.is_active = active


@router.get("/")
def list_models(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    active: bool | None = None,
    task_type: str | None = None,
    framework: str | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    if task_type is not None and task_type not in SUPPORTED_TASKS:
        raise HTTPException(status_code=422, detail=f"Unknown task_type; expected one of {SUPPORTED_TASKS}")
    if framework is not None and framework not in SUPPORTED_FRAMEWORKS:
        raise HTTPException(status_code=422, detail=f"Unknown framework; expected one of {SUPPORTED_FRAMEWORKS}")

    query = db.query(ModelVersion)
    if active is not None:
        query = query.filter(ModelVersion.is_active.is_(active))
    if task_type is not None:
        query = query.filter(ModelVersion.task_type == task_type)
    if framework is not None:
        query = query.filter(ModelVersion.framework == framework)

    total = query.count()
    items = query.order_by(ModelVersion.id).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [_serialize(model) for model in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (page * page_size) < total,
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_model(
    payload: ModelVersionCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    if db.query(ModelVersion).filter(ModelVersion.name == payload.name).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Model name already exists")

    checksum = _resolve(payload.source_path)

    model = ModelVersion(
        name=payload.name,
        description=payload.description,
        framework=payload.framework,
        framework_version=payload.framework_version,
        source_path=payload.source_path,
        device=payload.device,
        config_json=payload.config_json,
        status="unloaded",
        task_type=payload.task_type,
        artifact_id=payload.artifact_id or f"sha256:{checksum}",
        version=payload.version,
        license=payload.license,
        checksum=checksum,
        classes_json=payload.classes or None,
        thresholds_json=payload.thresholds,
        provenance_json=payload.provenance,
        is_active=False,
    )
    db.add(model)
    db.flush()
    if payload.is_active:
        _apply_active(db, model, True)
    db.commit()
    db.refresh(model)
    return _serialize(model)


@router.get("/{model_id}")
def get_model(
    model_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    return _serialize(_get_model(db, model_id))


@router.patch("/{model_id}")
def update_model(
    model_id: int,
    payload: ModelVersionUpdate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    model = _get_model(db, model_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return _serialize(model)

    if "source_path" in fields and fields["source_path"] is not None:
        checksum = _resolve(fields["source_path"])
        model.source_path = fields["source_path"]
        model.checksum = checksum
        if (model.artifact_id or "").startswith("sha256:"):
            model.artifact_id = f"sha256:{checksum}"

    for field in (
        "description",
        "framework",
        "framework_version",
        "task_type",
        "version",
        "license",
        "config_json",
    ):
        if field in fields:
            setattr(model, field, fields[field])
    if "artifact_id" in fields:
        model.artifact_id = fields["artifact_id"]
    if "classes" in fields:
        model.classes_json = fields["classes"] or None
    if "thresholds" in fields:
        model.thresholds_json = fields["thresholds"]
    if "provenance" in fields:
        model.provenance_json = fields["provenance"]
    if "is_active" in fields and fields["is_active"] is not None:
        _apply_active(db, model, bool(fields["is_active"]))

    db.commit()
    db.refresh(model)
    return _serialize(model)


@router.post("/{model_id}/activate")
def activate_model(
    model_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    model = _get_model(db, model_id)
    _apply_active(db, model, True)
    db.commit()
    db.refresh(model)
    return _serialize(model)


@router.post("/{model_id}/deactivate")
def deactivate_model(
    model_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    model = _get_model(db, model_id)
    _apply_active(db, model, False)
    db.commit()
    db.refresh(model)
    return _serialize(model)


@router.get("/{model_id}/evaluations")
def list_evaluations(
    model_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    model = _get_model(db, model_id)
    rows = (
        db.query(ModelEvaluation)
        .filter(ModelEvaluation.model_version_id == model.id)
        .order_by(ModelEvaluation.id.desc())
        .all()
    )
    return {"items": [_serialize_evaluation(row) for row in rows], "total": len(rows)}


@router.post("/{model_id}/evaluations", status_code=status.HTTP_201_CREATED)
def create_evaluation(
    model_id: int,
    payload: EvaluationIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*_WRITE_ROLES)),
) -> dict[str, Any]:
    """Record metrics only for a completed run on an identified split (T21 gate)."""
    model = _get_model(db, model_id)
    metrics = dict(payload.metrics_json)
    metrics.update(
        {
            "run_id": payload.run_id,
            "split": payload.split,
            "status": "completed",
        }
    )
    row = ModelEvaluation(
        model_version_id=model.id,
        task_type=payload.task_type,
        metrics_json=metrics,
        hardware=payload.hardware,
        input_size=payload.input_size,
        batch_size=payload.batch_size,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_evaluation(row)
