"""Cross-Correlation Engine between Vision AI & Food Equipment PdM (Fase 5: `correlation_engine.py`).

Monitors Vision AI defect spikes (e.g. > 3 `SEAL_DEFECT` events within 2 minutes)
and cross-references the latest telemetry from `HEAT_SEALER` / `FLOW_WRAPPER`
machines (`sealer_temp_c` normal 140–185°C, `pneumatic_pressure_bar` normal 5.5–7.0 bar).

When a vision defect spike coincides with a thermal or pneumatic drop, the engine
generates a unified diagnosis (e.g. "Lonjakan defect kemasan bocor (SEAL_DEFECT)
berkorelasi dengan penurunan suhu sealer bar menjadi 128°C") and automatically
mints a pending-human-approval `MaintenanceRequest`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Inspection, Machine, MaintenanceRequest, SensorReading
from app.pdm.schemas import (
    PNEUMATIC_PRESSURE_NORMAL_MIN_BAR,
    SEALER_TEMP_NORMAL_MIN_C,
    evaluate_food_equipment_anomalies,
)


def _is_seal_defect(ins: Inspection) -> bool:
    if (ins.defect_category or "").upper() in ("SEAL_DEFECT", "SEAL_LEAK"):
        return True
    meta_cats = [str(c).upper() for c in (ins.metadata_json or {}).get("defect_categories", [])]
    if "SEAL_DEFECT" in meta_cats or "SEAL_LEAK" in meta_cats:
        return True
    dets = (ins.detection_results_json or {}).get("detections", [])
    det_labels = {str(d.get("label", "")).upper() for d in dets if isinstance(d, dict)}
    if "SEAL_DEFECT" in det_labels or "SEAL_LEAK" in det_labels:
        return True
    if ins.anomaly_score_json and float(ins.anomaly_score_json.get("score") or 0.0) >= 0.58:
        return True
    return False


def evaluate_vision_pdm_correlation(
    db: Session,
    *,
    org_id: int,
    camera_id: int | None = None,
    machine_id: int | None = None,
    window_minutes: int = 2,
    min_seal_defects: int = 3,
    auto_create_request: bool = True,
    requested_by_user_id: int = 1,
) -> dict[str, Any]:
    """Cross-correlate recent Vision AI `SEAL_DEFECT` spikes with `HEAT_SEALER` / `FLOW_WRAPPER` telemetry."""
    mac_query = db.query(Machine).filter(Machine.org_id == org_id)
    machine: Machine | None = None
    if machine_id is not None:
        machine = mac_query.filter(Machine.id == machine_id).first()
    elif camera_id is not None:
        machine = mac_query.filter(Machine.camera_id == camera_id).first()
    if machine is None:
        machine = mac_query.order_by(Machine.id.asc()).first()

    resolved_cam_id = camera_id or (machine.camera_id if machine else None)

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=max(1, int(window_minutes)))
    ins_query = db.query(Inspection).filter(
        Inspection.org_id == org_id,
        Inspection.created_at >= since,
    )
    if resolved_cam_id is not None:
        ins_query = ins_query.filter(Inspection.camera_id == resolved_cam_id)
    recent_inspections = ins_query.order_by(Inspection.created_at.desc(), Inspection.id.desc()).limit(200).all()

    fail_count = 0
    seal_defect_count = 0
    for ins in recent_inspections:
        if ins.decision == "FAIL":
            fail_count += 1
        if _is_seal_defect(ins):
            seal_defect_count += 1

    latest_reading: SensorReading | None = None
    if machine is not None:
        latest_reading = (
            db.query(SensorReading)
            .filter(SensorReading.machine_id == machine.id)
            .order_by(SensorReading.recorded_at.desc(), SensorReading.id.desc())
            .first()
        )

    food_metrics = latest_reading.food_features() if latest_reading is not None else {}
    failure_modes = evaluate_food_equipment_anomalies(food_metrics) if food_metrics else []
    sealer_c = food_metrics.get("sealer_temp_c")
    pressure_bar = food_metrics.get("pneumatic_pressure_bar")

    has_thermal_drop = sealer_c is not None and float(sealer_c) < SEALER_TEMP_NORMAL_MIN_C
    has_pneumatic_drop = pressure_bar is not None and float(pressure_bar) < PNEUMATIC_PRESSURE_NORMAL_MIN_BAR
    has_machine_anomaly = bool(has_thermal_drop or has_pneumatic_drop or failure_modes)
    has_vision_spike = seal_defect_count >= min_seal_defects or fail_count > min_seal_defects

    correlated = bool(has_machine_anomaly and (seal_defect_count >= 1 if min_seal_defects <= 1 else has_vision_spike))

    if correlated:
        if has_thermal_drop:
            temp_fmt = f"{float(sealer_c):g}"
            diagnosis = (
                f"Lonjakan defect kemasan bocor (SEAL_DEFECT) berkorelasi dengan penurunan suhu sealer bar menjadi {temp_fmt}°C"
            )
            if has_pneumatic_drop:
                diagnosis += f" dan tekanan pneumatik drop ke {float(pressure_bar):g} bar"
        elif has_pneumatic_drop:
            diagnosis = (
                f"Lonjakan defect kemasan bocor (SEAL_DEFECT) berkorelasi dengan penurunan tekanan pneumatik menjadi {float(pressure_bar):g} bar"
            )
        else:
            diagnosis = (
                f"Lonjakan defect kemasan bocor (SEAL_DEFECT) berkorelasi dengan anomali mesin {', '.join(failure_modes)}"
            )
    else:
        diagnosis = "Parameter mesin dan inspeksi visual dalam batas kendali normal."

    created_req_id: int | None = None
    created_req_status: str | None = None
    if correlated and auto_create_request and machine is not None:
        existing_pending = (
            db.query(MaintenanceRequest)
            .filter(
                MaintenanceRequest.org_id == org_id,
                MaintenanceRequest.machine_id == machine.id,
                MaintenanceRequest.status == "pending",
            )
            .order_by(MaintenanceRequest.id.desc())
            .first()
        )
        if existing_pending is not None and "SEAL_DEFECT" in (existing_pending.title or ""):
            created_req_id = existing_pending.id
            created_req_status = existing_pending.status
        else:
            req = MaintenanceRequest(
                org_id=org_id,
                machine_id=machine.id,
                requested_by_user_id=requested_by_user_id,
                title=diagnosis[:200],
                description=(
                    f"{diagnosis}. Terdeteksi {seal_defect_count}x SEAL_DEFECT dalam {window_minutes} menit terakhir "
                    f"pada Kamera #{resolved_cam_id}. Menunggu persetujuan manusia (Human-in-the-Loop)."
                ),
                priority="critical",
                status="pending",
            )
            db.add(req)
            db.commit()
            db.refresh(req)
            created_req_id = req.id
            created_req_status = req.status

    return {
        "correlated": correlated,
        "spike_detected": has_vision_spike,
        "camera_id": resolved_cam_id,
        "machine_id": machine.id if machine else None,
        "machine_code": machine.code if machine else None,
        "machine_type": machine.machine_type if machine else None,
        "window_minutes": window_minutes,
        "recent_fail_count": fail_count,
        "seal_defect_count": seal_defect_count,
        "seal_leak_count": seal_defect_count,
        "sealer_temp_c": sealer_c,
        "pneumatic_pressure_bar": pressure_bar,
        "failure_modes": failure_modes,
        "diagnosis": diagnosis,
        "maintenance_request_id": created_req_id,
        "maintenance_request_status": created_req_status,
    }
