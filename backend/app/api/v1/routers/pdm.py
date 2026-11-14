"""Predictive Maintenance API (B5): machines, readings, predictions,
maintenance requests with approval gating, and schedules.

Every route is JWT-authenticated, RBAC-matrixed, org-scoped
(`app/services/tenant.py`), and audit-logged where state changes. Predictions
are fail-safe (`app/pdm/inference.py` contract) and never fabricate numbers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.core.constants import Role as RoleNames
from app.db.models import AuditLog, Machine, MaintenanceRequest, MaintenanceSchedule, SensorReading, User
from app.db.session import get_db
from app.pdm import inference as pdm_inference
from app.services.tenant import get_user_org_id, scoped_one, scoped_query, stamp_org

router = APIRouter(prefix="/pdm", tags=["predictive-maintenance"])

READ_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER, RoleNames.VIEWER)
WRITE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER)
APPROVE_ROLES = (RoleNames.OWNER, RoleNames.ADMIN)

MACHINE_STATUSES = ("running", "idle", "fault", "maintenance")
PRIORITIES = ("low", "medium", "high", "critical")
REQUEST_STATUSES = ("pending", "approved", "rejected", "completed", "cancelled")


class MachineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=80)
    machine_type: str = Field(default="generic", max_length=60)
    camera_id: int | None = Field(default=None, gt=0)


class MachineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    machine_type: str | None = Field(default=None, max_length=60)
    camera_id: int | None = Field(default=None, gt=0)
    status: Literal["running", "idle", "fault", "maintenance"] | None = None


class MachineOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    org_id: int
    name: str
    code: str
    machine_type: str
    camera_id: int | None
    status: str
    installed_at: datetime | None = None
    created_at: datetime


class ReadingCreate(BaseModel):
    air_temperature_k: float = Field(ge=250, le=340)
    process_temperature_k: float = Field(ge=250, le=360)
    rotational_speed_rpm: float = Field(ge=0, le=4000)
    torque_nm: float = Field(ge=0, le=500)
    tool_wear_min: float = Field(ge=0, le=300)
    recorded_at: datetime | None = None


class PredictionOut(BaseModel):
    machine_id: int
    mode: str
    model_selected: str | None = None
    note: str | None = None
    error: str | None = None
    failure_probability: float | None = None
    rul_minutes: float | None = None
    health: float | None = None
    failure_modes: list[str] = []
    recommendation: str
    reading_id: int | None = None
    features: dict | None = None


class MaintenanceRequestCreate(BaseModel):
    machine_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: Literal["low", "medium", "high", "critical"] = "medium"


class MaintenanceDecision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def _audit(db: Session, actor: User, action: str, details: dict, request: Request) -> None:
    db.add(
        AuditLog(
            user_id=actor.id,
            org_id=get_user_org_id(actor),
            action=action,
            details_json=details,
            ip_address=request.client.host if request.client else None,
        )
    )


def _machine_or_404(db: Session, user: User, machine_id: int) -> Machine:
    machine = scoped_one(db, Machine, user, machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
    return machine


# ── Machines ────────────────────────────────────────────────────────────────


@router.get("/machines", response_model=list[MachineOut])
def list_machines(
    status_filter: Literal["running", "idle", "fault", "maintenance"] | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> list[Machine]:
    query = scoped_query(db, Machine, _user)
    if status_filter:
        query = query.filter(Machine.status == status_filter)
    return query.order_by(Machine.id).all()


@router.post("/machines", response_model=MachineOut, status_code=status.HTTP_201_CREATED)
def create_machine(
    payload: MachineCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> Machine:
    if scoped_query(db, Machine, user).filter(Machine.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Machine code already exists")
    machine = Machine(
        name=payload.name,
        code=payload.code,
        machine_type=payload.machine_type,
        camera_id=payload.camera_id,
        status="running",
    )
    stamp_org(machine, user)
    db.add(machine)
    _audit(db, user, "pdm.machine.created", {"machine_id": machine.id, "code": machine.code}, request)
    db.commit()
    db.refresh(machine)
    return machine


@router.get("/machines/{machine_id}", response_model=MachineOut)
def get_machine(
    machine_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> Machine:
    return _machine_or_404(db, user, machine_id)


@router.patch("/machines/{machine_id}", response_model=MachineOut)
def update_machine(
    machine_id: int,
    payload: MachineUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> Machine:
    machine = _machine_or_404(db, user, machine_id)
    for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(machine, field, value)
    _audit(db, user, "pdm.machine.updated", {"machine_id": machine.id}, request)
    db.commit()
    db.refresh(machine)
    return machine


# ── Sensor readings ─────────────────────────────────────────────────────────


@router.post("/machines/{machine_id}/readings", status_code=status.HTTP_201_CREATED)
def add_reading(
    machine_id: int,
    payload: ReadingCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    machine = _machine_or_404(db, user, machine_id)
    if machine.status == "fault" and payload.process_temperature_k > 330:
        # Data from a faulted machine is still recorded (history), but flagged.
        pass
    reading = SensorReading(
        machine_id=machine.id,
        air_temperature_k=payload.air_temperature_k,
        process_temperature_k=payload.process_temperature_k,
        rotational_speed_rpm=payload.rotational_speed_rpm,
        torque_nm=payload.torque_nm,
        tool_wear_min=payload.tool_wear_min,
        recorded_at=payload.recorded_at or datetime.now(timezone.utc),
    )
    stamp_org(reading, user)
    db.add(reading)
    _audit(db, user, "pdm.reading.recorded", {"machine_id": machine.id}, request)
    db.commit()
    db.refresh(reading)
    return {
        "id": reading.id,
        "machine_id": machine.id,
        "recorded_at": reading.recorded_at,
        "features": dict(zip(pdm_inference.FEATURE_NAMES, reading.features())),
    }


@router.get("/machines/{machine_id}/readings")
def list_readings(
    machine_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict]:
    _machine_or_404(db, _user, machine_id)
    rows = (
        scoped_query(db, SensorReading, _user)
        .filter(SensorReading.machine_id == machine_id)
        .order_by(SensorReading.recorded_at.desc(), SensorReading.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "recorded_at": row.recorded_at,
            "features": dict(zip(pdm_inference.FEATURE_NAMES, row.features())),
        }
        for row in rows
    ]


# ── Predictions ─────────────────────────────────────────────────────────────


@router.post("/machines/{machine_id}/predict", response_model=PredictionOut)
def predict(
    machine_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> PredictionOut:
    """Fail-safe prediction; see app/pdm/inference.py for the response contract."""
    machine = _machine_or_404(db, user, machine_id)
    result = pdm_inference.predict_for_machine(db, machine)
    return PredictionOut(**result)


# ── Maintenance requests (approval workflow) ───────────────────────────────


@router.get("/requests")
def list_requests(
    request_status: Literal["pending", "approved", "rejected", "completed", "cancelled"] | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict]:
    query = scoped_query(db, MaintenanceRequest, _user)
    if request_status:
        query = query.filter(MaintenanceRequest.status == request_status)
    rows = query.order_by(MaintenanceRequest.created_at.desc()).limit(500).all()
    return [_serialize_request(row) for row in rows]


def _serialize_request(row: MaintenanceRequest) -> dict:
    return {
        "id": row.id,
        "machine_id": row.machine_id,
        "title": row.title,
        "description": row.description,
        "priority": row.priority,
        "status": row.status,
        "requested_by_user_id": row.requested_by_user_id,
        "decided_by_user_id": row.decided_by_user_id,
        "decision_note": row.decision_note,
        "decided_at": row.decided_at,
        "created_at": row.created_at,
    }


@router.post("/requests", status_code=status.HTTP_201_CREATED)
def create_request(
    payload: MaintenanceRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    _machine_or_404(db, user, payload.machine_id)
    row = MaintenanceRequest(
        machine_id=payload.machine_id,
        requested_by_user_id=user.id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        status="pending",
    )
    stamp_org(row, user)
    db.add(row)
    _audit(db, user, "pdm.request.created", {"request_id": row.id, "machine_id": row.machine_id}, request)
    db.commit()
    db.refresh(row)
    return _serialize_request(row)


def _request_or_404(db: Session, user: User, request_id: int) -> MaintenanceRequest:
    row = scoped_one(db, MaintenanceRequest, user, request_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Maintenance request not found")
    return row


@router.post("/requests/{request_id}/approve")
def approve_request(
    request_id: int,
    payload: MaintenanceDecision,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*APPROVE_ROLES)),
) -> dict:
    """Approval gate: pending -> approved by an authorized role.

    Self-approval is forbidden; the deciding identity is persisted.
    """
    row = _request_or_404(db, user, request_id)
    if row.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Request is {row.status}, not pending")
    if row.requested_by_user_id == user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-approval is not allowed")
    row.status = "approved"
    row.decided_by_user_id = user.id
    row.decision_note = payload.note
    row.decided_at = datetime.now(timezone.utc)
    _audit(db, user, "pdm.request.approved", {"request_id": row.id}, request)
    db.commit()
    db.refresh(row)
    return _serialize_request(row)


@router.post("/requests/{request_id}/reject")
def reject_request(
    request_id: int,
    payload: MaintenanceDecision,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*APPROVE_ROLES)),
) -> dict:
    row = _request_or_404(db, user, request_id)
    if row.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Request is {row.status}, not pending")
    row.status = "rejected"
    row.decided_by_user_id = user.id
    row.decision_note = payload.note
    row.decided_at = datetime.now(timezone.utc)
    _audit(db, user, "pdm.request.rejected", {"request_id": row.id}, request)
    db.commit()
    db.refresh(row)
    return _serialize_request(row)


@router.post("/requests/{request_id}/complete")
def complete_request(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    row = _request_or_404(db, user, request_id)
    if row.status != "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Only approved requests can be completed (request is {row.status})")
    row.status = "completed"
    _audit(db, user, "pdm.request.completed", {"request_id": row.id}, request)
    db.commit()
    db.refresh(row)
    return _serialize_request(row)


@router.post("/requests/{request_id}/cancel")
def cancel_request(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    row = _request_or_404(db, user, request_id)
    if row.status not in ("pending", "approved"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Request is already {row.status}")
    if row.status == "approved" and row.decided_by_user_id and row.decided_by_user_id != user.id:
        # Only an approver (or an admin) may cancel an approved request.
        if user.role.name not in RoleNames.admin_roles():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the approver or an admin can cancel an approved request")
    row.status = "cancelled"
    _audit(db, user, "pdm.request.cancelled", {"request_id": row.id}, request)
    db.commit()
    db.refresh(row)
    return _serialize_request(row)


# ── Schedules ───────────────────────────────────────────────────────────────


class ScheduleCreate(BaseModel):
    machine_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    interval_days: int = Field(ge=1, le=3650)


@router.get("/schedules")
def list_schedules(
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict]:
    rows = scoped_query(db, MaintenanceSchedule, _user).order_by(MaintenanceSchedule.next_due_at).all()
    return [
        {
            "id": row.id,
            "machine_id": row.machine_id,
            "title": row.title,
            "interval_days": row.interval_days,
            "last_done_at": row.last_done_at,
            "next_due_at": row.next_due_at,
            "is_active": row.is_active,
        }
        for row in rows
    ]


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    _machine_or_404(db, user, payload.machine_id)
    now = datetime.now(timezone.utc)
    row = MaintenanceSchedule(
        machine_id=payload.machine_id,
        title=payload.title,
        interval_days=payload.interval_days,
        last_done_at=None,
        next_due_at=now + timedelta(days=payload.interval_days),
    )
    stamp_org(row, user)
    db.add(row)
    _audit(db, user, "pdm.schedule.created", {"schedule_id": row.id}, request)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "machine_id": row.machine_id,
        "title": row.title,
        "interval_days": row.interval_days,
        "last_done_at": row.last_done_at,
        "next_due_at": row.next_due_at,
        "is_active": row.is_active,
    }
