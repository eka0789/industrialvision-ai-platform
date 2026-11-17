"""Operational Technology (OT) Bridge API (Fase 4: `backend/app/api/v1/routers/ot.py`).

Exposes endpoints for:
- Photoelectric sensor conveyor hardware triggers (`POST /ot/trigger/{camera_id}`)
- PLC & Actuator I/O configuration per station (`PATCH /ot/cameras/{camera_id}/config`)
- Test Rejector Pulse (`POST /ot/cameras/{camera_id}/test-pulse` — RBAC gated to
  owner/admin/ai_engineer with AuditLog)
- PLC rejector actuation audit log (`GET /ot/events`)
- OT Bridge status and `trigger_to_reject_ms` latency telemetry (`GET /ot/status`)
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.v1.deps import require_roles
from app.core.config import settings
from app.core.constants import Role as RoleNames
from app.db.models import AuditLog, Camera, OTTriggerEvent, User
from app.db.session import get_db
from app.ot.adapter import ActuatorType, OTAdapter, OTProtocol, execute_hardware_trigger
from app.schemas.camera import sanitize_ot_host
from app.services.tenant import get_user_org_id, scoped_one, scoped_query

router = APIRouter(prefix="/ot", tags=["ot-bridge"])

READ_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER, RoleNames.VIEWER)
TRIGGER_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.OPERATOR, RoleNames.AI_ENGINEER)
ENGINEER_ADMIN_ROLES = (RoleNames.OWNER, RoleNames.ADMIN, RoleNames.AI_ENGINEER)


class HardwareTriggerRequest(BaseModel):
    sensor_id: str = Field(default="PHOTO-SENS-01", min_length=1, max_length=80)
    protocol: OTProtocol | None = None
    actuator_type: ActuatorType = "pneumatic_air_blast"
    sku_code: str | None = Field(default=None, max_length=80)
    simulate_defect: Literal["seal_leak", "missing_exp_date", "foreign_matter", "burnt"] | None = None
    simulate_blur: bool = False


class CameraOTConfigUpdate(BaseModel):
    ot_protocol: Literal["simulated", "modbus_tcp", "mqtt"] = "simulated"
    ot_host: str | None = Field(default="127.0.0.1", max_length=255)
    ot_port: int | None = Field(default=502, ge=1, le=65535)
    trigger_register: int | None = Field(default=1, ge=0, le=65535)
    reject_coil: int | None = Field(default=100, ge=0, le=65535)
    reject_delay_ms: int = Field(default=120, ge=0, le=10000)
    reject_pulse_ms: int = Field(default=45, ge=5, le=5000)


class TestRejectorPulseRequest(BaseModel):
    actuator_type: ActuatorType = "pneumatic_air_blast"
    pulse_ms: int | None = Field(default=None, ge=5, le=5000)
    reject_coil: int | None = Field(default=None, ge=0, le=65535)


@router.patch("/cameras/{camera_id}/config")
def update_camera_ot_config(
    camera_id: int,
    payload: CameraOTConfigUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ENGINEER_ADMIN_ROLES)),
) -> dict[str, Any]:
    """Update PLC & Actuator I/O configuration on a camera station (redacting credentials in response)."""
    cam = scoped_one(db, Camera, user, camera_id)
    if cam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    cam.ot_protocol = payload.ot_protocol
    cam.ot_host = payload.ot_host
    cam.ot_port = payload.ot_port
    cam.trigger_register = payload.trigger_register
    cam.reject_coil = payload.reject_coil
    cam.reject_delay_ms = payload.reject_delay_ms
    cam.reject_pulse_ms = payload.reject_pulse_ms

    safe_host = sanitize_ot_host(cam.ot_host)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=get_user_org_id(user),
            action="ot.camera.config_updated",
            details_json={
                "camera_id": cam.id,
                "ot_protocol": cam.ot_protocol,
                "ot_host": safe_host,
                "ot_port": cam.ot_port,
                "trigger_register": cam.trigger_register,
                "reject_coil": cam.reject_coil,
                "reject_delay_ms": cam.reject_delay_ms,
                "reject_pulse_ms": cam.reject_pulse_ms,
            },
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(cam)
    return {
        "camera_id": cam.id,
        "ot_protocol": cam.ot_protocol,
        "ot_host": safe_host,
        "ot_port": cam.ot_port,
        "trigger_register": cam.trigger_register,
        "reject_coil": cam.reject_coil,
        "reject_delay_ms": cam.reject_delay_ms,
        "reject_pulse_ms": cam.reject_pulse_ms,
    }


@router.post("/cameras/{camera_id}/test-pulse")
def test_rejector_pulse(
    camera_id: int,
    payload: TestRejectorPulseRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*ENGINEER_ADMIN_ROLES)),
) -> dict[str, Any]:
    """Fire a test rejector pulse (restricted to admin & ai_engineer with AuditLog)."""
    cam = scoped_one(db, Camera, user, camera_id)
    if cam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    org_id = get_user_org_id(user)
    coil = int(payload.reject_coil if payload.reject_coil is not None else (cam.reject_coil or 100))
    pulse = int(payload.pulse_ms if payload.pulse_ms is not None else (cam.reject_pulse_ms or settings.OT_REJECT_PULSE_MS))
    proto: OTProtocol = (cam.ot_protocol or "simulated")  # type: ignore[assignment]

    adapter = OTAdapter(
        protocol=proto,
        plc_host=cam.ot_host or settings.OT_PLC_HOST,
        plc_port=cam.ot_port or settings.OT_PLC_PORT,
        mqtt_broker=cam.ot_host or settings.OT_MQTT_BROKER,
        pulse_ms=pulse,
        org_id=org_id,
        line_id=cam.line_id or 1,
    )
    actuation = adapter.actuate_rejector(
        camera_id=cam.id,
        inspection_id=0,
        decision="FAIL",
        actuator_type=payload.actuator_type,
        coil_address=coil,
    )

    ot_event = OTTriggerEvent(
        org_id=org_id,
        camera_id=cam.id,
        inspection_id=None,
        protocol=proto,
        sensor_id="MANUAL-TEST-PULSE",
        decision="TEST_PULSE",
        rejector_actuated=True,
        actuator_type=payload.actuator_type,
        plc_target=actuation["plc_target"],
        inference_latency_ms=0.0,
        total_cycle_ms=float(cam.reject_delay_ms or 120),
        details_json={
            "mode": "test_rejector_pulse",
            "reject_coil": coil,
            "pulse_ms": pulse,
            "reject_delay_ms": cam.reject_delay_ms or 120,
            "trigger_to_reject_ms": float(cam.reject_delay_ms or 120),
            "transport_status": actuation["transport_status"],
            "tested_by": user.username,
        },
    )
    db.add(ot_event)
    db.add(
        AuditLog(
            user_id=user.id,
            org_id=org_id,
            action="ot.rejector.test_pulse",
            details_json={
                "camera_id": cam.id,
                "protocol": proto,
                "reject_coil": coil,
                "pulse_ms": pulse,
                "actuator_type": payload.actuator_type,
                "plc_target": actuation["plc_target"],
            },
            ip_address=request.client.host if request.client else None,
        )
    )
    db.commit()
    db.refresh(ot_event)

    return {
        "status": "ok",
        "trigger_event_id": ot_event.id,
        "camera_id": cam.id,
        "protocol": proto,
        "reject_coil": coil,
        "pulse_ms": pulse,
        "reject_delay_ms": cam.reject_delay_ms or 120,
        "rejector": actuation,
    }


@router.post("/trigger/{camera_id}", status_code=status.HTTP_201_CREATED)
def trigger_conveyor_inspection(
    camera_id: int,
    payload: HardwareTriggerRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*TRIGGER_ROLES)),
) -> dict[str, Any]:
    """Execute one photoelectric sensor trigger -> 1 frame -> ROI inference -> PLC rejector pulse."""
    cam = scoped_one(db, Camera, user, camera_id)
    if cam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    result = execute_hardware_trigger(
        db,
        user=user,
        camera_id=cam.id,
        sensor_id=payload.sensor_id,
        protocol=payload.protocol,
        actuator_type=payload.actuator_type,
        sku_code=payload.sku_code,
        simulate_defect=payload.simulate_defect,
        simulate_blur=payload.simulate_blur,
    )
    if result["rejector"]["actuated"] or result["preprocessing"]["is_blurred"]:
        db.add(
            AuditLog(
                user_id=user.id,
                org_id=get_user_org_id(user),
                action="ot.rejector.actuated" if result["rejector"]["actuated"] else "ot.camera.blur_alert",
                details_json={
                    "camera_id": cam.id,
                    "inspection_id": result["inspection_id"],
                    "decision": result["decision"],
                    "actuator": payload.actuator_type,
                    "protocol": payload.protocol or cam.ot_protocol,
                    "trigger_to_reject_ms": result.get("trigger_to_reject_ms"),
                },
                ip_address=request.client.host if request.client else None,
            )
        )
        db.commit()
    return result


@router.get("/events")
def list_ot_events(
    camera_id: int | None = Query(default=None, gt=0),
    actuated_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> list[dict[str, Any]]:
    query = scoped_query(db, OTTriggerEvent, user)
    if camera_id is not None:
        query = query.filter(OTTriggerEvent.camera_id == camera_id)
    if actuated_only:
        query = query.filter(OTTriggerEvent.rejector_actuated.is_(True))
    rows = query.order_by(OTTriggerEvent.created_at.desc(), OTTriggerEvent.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "camera_id": r.camera_id,
            "inspection_id": r.inspection_id,
            "protocol": r.protocol,
            "sensor_id": r.sensor_id,
            "decision": r.decision,
            "rejector_actuated": r.rejector_actuated,
            "actuator_type": r.actuator_type,
            "plc_target": r.plc_target,
            "inference_latency_ms": r.inference_latency_ms,
            "total_cycle_ms": r.total_cycle_ms,
            "trigger_to_reject_ms": (r.details_json or {}).get("trigger_to_reject_ms", r.total_cycle_ms),
            "details": r.details_json or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/status")
def get_ot_bridge_status(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> dict[str, Any]:
    org_id = get_user_org_id(user)
    total_triggers = db.query(func.count(OTTriggerEvent.id)).filter(OTTriggerEvent.org_id == org_id).scalar() or 0
    total_rejects = (
        db.query(func.count(OTTriggerEvent.id))
        .filter(OTTriggerEvent.org_id == org_id, OTTriggerEvent.rejector_actuated.is_(True))
        .scalar()
        or 0
    )
    avg_inf = (
        db.query(func.avg(OTTriggerEvent.inference_latency_ms))
        .filter(OTTriggerEvent.org_id == org_id)
        .scalar()
    )
    avg_cycle = (
        db.query(func.avg(OTTriggerEvent.total_cycle_ms))
        .filter(OTTriggerEvent.org_id == org_id)
        .scalar()
    )
    max_cycle = (
        db.query(func.max(OTTriggerEvent.total_cycle_ms))
        .filter(OTTriggerEvent.org_id == org_id)
        .scalar()
    )
    return {
        "default_protocol": settings.OT_DEFAULT_PROTOCOL,
        "supported_protocols": ["simulated", "modbus_tcp", "mqtt", "gpio_serial"],
        "supported_actuators": ["pneumatic_air_blast", "diverter_arm"],
        "plc_endpoint": f"{settings.OT_PLC_HOST}:{settings.OT_PLC_PORT}",
        "reject_pulse_ms": settings.OT_REJECT_PULSE_MS,
        "target_inference_ms": settings.OT_TARGET_INFERENCE_MS,
        "total_triggers": int(total_triggers),
        "total_rejector_actuations": int(total_rejects),
        "avg_inference_latency_ms": round(float(avg_inf), 2) if avg_inf is not None else None,
        "avg_total_cycle_ms": round(float(avg_cycle), 2) if avg_cycle is not None else None,
        "avg_trigger_to_reject_ms": round(float(avg_cycle), 2) if avg_cycle is not None else None,
        "max_trigger_to_reject_ms": round(float(max_cycle), 2) if max_cycle is not None else None,
    }
