"""Demo data seeder for IndustrialVision AI.

Populates the database with a full, realistic demo set: roles and demo users,
production lines, inspection stations (cameras), an AI model registry with
evaluation history, hundreds of traceable inspections with synthetic evidence
images, plus system events and audit logs.

Usage (from ``backend/``)::

    python -m app.seed              # seed if empty
    python -m app.seed --reset      # wipe everything and reseed
    python -m app.seed --append     # add more inspections to existing data
    python -m app.seed --inspections 500 --hours 168

Demo accounts (password documented in README, change for anything public):

    admin      / Admin!Vision2026      (admin)
    operator1  / Operator!2026         (operator)
    aiengineer / Engineer!2026         (ai_engineer)
"""

from __future__ import annotations

import argparse
import random
import shutil
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy.orm import Session

from app.core.constants import CameraSourceType, CameraStatus, Decision, Role as RoleNames
from app.db.models import (
    AnomalyResult,
    AuditLog,
    Camera,
    DetectionResult,
    Inspection,
    InspectionSession,
    ModelEvaluation,
    ModelVersion,
    ProductionLine,
    Role,
    SystemEvent,
    User,
)
from app.core.config import settings
from app.db.session import SessionLocal
from app.services.decision import DecisionRules, decide_inspection
from app.services.evidence import EvidenceStorage
from app.services.inspection_manager import _rules_for_model
from app.services.synthetic_media import render_part_image

DEMO_USERS: dict[str, tuple[str, str]] = {
    RoleNames.ADMIN: ("admin", "Admin!Vision2026"),
    RoleNames.OPERATOR: ("operator1", "Operator!2026"),
    RoleNames.AI_ENGINEER: ("aiengineer", "Engineer!2026"),
}

DETECTION_CLASSES = ["scratch", "dent", "crack", "contamination"]
REJECT_LABELS = ["crack", "contamination"]

ERROR_PROFILES = (
    ("inference_failed", "ONNX inference timed out after 15000 ms"),
    ("evidence_storage_failed", "Evidence storage rejected the write: disk quota exceeded"),
    ("artifact_mismatch", "Model artifact failed checksum verification"),
)

PRODUCT_CODES = ("BRK-2041-A", "BRK-2041-B", "CVR-1180-C", "HSG-0904-A", "SFT-3310-D")

# Typed acknowledgement required by --reset, so a stray flag cannot wipe user accounts.
RESET_CONFIRMATION = "WIPE-ALL-DATA"


def _later(current: datetime | None, candidate: datetime) -> datetime:
    """Return the newer timestamp, tolerating naive DB values (SQLite)."""
    if current is None:
        return candidate
    current_cmp = current.replace(tzinfo=None) if current.tzinfo else current
    candidate_cmp = candidate.replace(tzinfo=None) if candidate.tzinfo else candidate
    return candidate if candidate_cmp > current_cmp else current


def _sha256_fake(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(64))


def _ensure_roles(db: Session) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    descriptions = {
        RoleNames.ADMIN: "Administrator role",
        RoleNames.OPERATOR: "Operator role",
        RoleNames.AI_ENGINEER: "AI engineer role",
    }
    for name in RoleNames.all():
        role = db.query(Role).filter(Role.name == name).first()
        if role is None:
            role = Role(name=name, description=descriptions[name])
            db.add(role)
            db.flush()
        roles[name] = role
    return roles


def _seed_users(db: Session, roles: dict[str, Role], rng: random.Random) -> list[User]:
    created: list[User] = []
    for role_name, (username, password) in DEMO_USERS.items():
        if db.query(User).filter(User.username == username).first():
            continue
        user = User(
            username=username,
            email=f"{username}@industrialvision.demo",
            hashed_password="",
            role_id=roles[role_name].id,
            is_active=True,
        )
        user.set_password(password)
        db.add(user)
        created.append(user)
        db.add(
            AuditLog(
                user_id=None,
                action="auth.bootstrap_demo_user",
                details_json={"username": username, "role": role_name},
                ip_address="127.0.0.1",
            )
        )
    db.flush()
    return created


def _seed_lines_and_cameras(db: Session, rng: random.Random) -> list[Camera]:
    if db.query(ProductionLine).count():
        return db.query(Camera).order_by(Camera.id).all()

    lines = [
        ProductionLine(name="Line 01 — Stamping", description="Bracket stamping and forming cell"),
        ProductionLine(name="Line 02 — Coating", description="Powder coating and curing tunnel"),
        ProductionLine(name="Line 03 — Final Assembly", description="Assembly, verification, and packing"),
    ]
    db.add_all(lines)
    db.flush()

    cameras = [
        Camera(
            name="Bracket stamping cam", camera_identity="CAM-L01-01",
            source_type=CameraSourceType.UPLOAD, line_id=lines[0].id,
            resolution="1920x1080", fps_input=25,
            status=CameraStatus.CONNECTED, is_active=True,
        ),
        Camera(
            name="Surface finish cam A", camera_identity="CAM-L01-02",
            source_type=CameraSourceType.SIMULATED, line_id=lines[0].id,
            resolution="1920x1080", fps_input=25,
            status=CameraStatus.CONNECTED, is_active=True,
        ),
        Camera(
            name="Coating defect cam", camera_identity="CAM-L02-01",
            source_type=CameraSourceType.SIMULATED, line_id=lines[1].id,
            resolution="1600x1200", fps_input=20,
            status=CameraStatus.CONNECTED, is_active=True,
        ),
        Camera(
            name="Assembly verify cam", camera_identity="CAM-L03-01",
            source_type=CameraSourceType.SIMULATED, line_id=lines[2].id,
            resolution="1920x1080", fps_input=25,
            status=CameraStatus.CONNECTED, is_active=True,
        ),
        Camera(
            name="Robotic cell cam", camera_identity="CAM-L03-02",
            source_type=CameraSourceType.RTSP,
            source_url="rtsp://192.168.10.41:554/stream1", line_id=lines[2].id,
            resolution="1280x720", fps_input=15,
            status=CameraStatus.DEGRADED, is_active=True,
            last_error="Stream timeout after 5.0 s — reconnecting",
        ),
        Camera(
            name="Packaging QC cam", camera_identity="CAM-L02-02",
            source_type=CameraSourceType.WEBCAM, source_url="0", line_id=lines[1].id,
            resolution="1280x720", fps_input=30,
            status=CameraStatus.DISCONNECTED, is_active=False,
        ),
    ]
    db.add_all(cameras)
    db.flush()
    return cameras


def _seed_models(db: Session, rng: random.Random) -> list[ModelVersion]:
    if db.query(ModelVersion).count():
        return db.query(ModelVersion).order_by(ModelVersion.id).all()

    det_checksum = _sha256_fake(rng)
    det_checksum_old = _sha256_fake(rng)
    anomaly_checksum = _sha256_fake(rng)
    padim_checksum = _sha256_fake(rng)

    models = [
        ModelVersion(
            name="surface-defect-yolov8n",
            description="YOLOv8n detector for stamping surface defects (scratch/dent/crack/contamination)",
            framework="yolo", framework_version="8.3.47",
            source_path="yolov8n-surface-defect.pt", device="cpu",
            status="loaded", task_type="object_detection",
            artifact_id=f"sha256:{det_checksum}", version="v2.3.1",
            license="AGPL-3.0", checksum=det_checksum,
            classes_json=DETECTION_CLASSES,
            thresholds_json={"detection_confidence": 0.5},
            provenance_json={"dataset": "surface-defects-2026-06", "train_images": 12400, "augment": "mosaic+HSV"},
            config_json={
                "reject_labels": REJECT_LABELS,
                "detection_confidence": 0.5,
                "review_band": 0.05,
            },
            is_active=True,
        ),
        ModelVersion(
            name="surface-defect-yolov8n-legacy",
            description="Previous production detector — kept for traceability comparisons",
            framework="yolo", framework_version="8.3.20",
            source_path="yolov8n-surface-defect-v2.2.pt", device="cpu",
            status="unloaded", task_type="object_detection",
            artifact_id=f"sha256:{det_checksum_old}", version="v2.2.0",
            license="AGPL-3.0", checksum=det_checksum_old,
            classes_json=DETECTION_CLASSES,
            thresholds_json={"detection_confidence": 0.5},
            provenance_json={"dataset": "surface-defects-2025-11", "train_images": 9800},
            config_json={"reject_labels": REJECT_LABELS, "detection_confidence": 0.55},
            is_active=False,
        ),
        ModelVersion(
            name="patchcore-steel-anomaly",
            description="PatchCore image-level anomaly scoring for coating uniformity",
            framework="onnx", framework_version="1.16.3",
            source_path="patchcore-steel.onnx", device="cpu",
            status="loaded", task_type="anomaly_image",
            artifact_id=f"sha256:{anomaly_checksum}", version="v1.4.0",
            license="Apache-2.0", checksum=anomaly_checksum,
            classes_json=["anomaly"],
            thresholds_json={"anomaly_threshold": 0.62},
            provenance_json={"dataset": "coating-uniformity-2026-04", "good_images": 2100, "backbone": "WideResNet-50"},
            config_json={"anomaly_threshold": 0.62, "review_band": 0.08},
            is_active=True,
        ),
        ModelVersion(
            name="padim-aluminum-anomaly",
            description="PaDiM baseline for aluminum housings — superseded by PatchCore",
            framework="onnx", framework_version="1.16.3",
            source_path="padim-aluminum.onnx", device="cpu",
            status="unloaded", task_type="anomaly_image",
            artifact_id=f"sha256:{padim_checksum}", version="v0.9.2",
            license="MIT", checksum=padim_checksum,
            classes_json=["anomaly"],
            thresholds_json={"anomaly_threshold": 0.7},
            provenance_json={"dataset": "housing-surfaces-2025-09", "good_images": 1500},
            config_json={"anomaly_threshold": 0.7, "review_band": 0.06},
            is_active=False,
        ),
    ]
    db.add_all(models)
    db.flush()

    base = datetime.now(timezone.utc) - timedelta(days=21)
    evaluations = [
        ModelEvaluation(
            model_version_id=models[0].id, task_type="object_detection",
            metrics_json={"mAP50": 0.942, "precision": 0.951, "recall": 0.918, "f1": 0.934,
                          "run_id": "run-2026-06-12-a", "split": "validation", "status": "completed"},
            hardware="Intel Xeon W-1350 (CPU)", input_size="640x640", batch_size=8,
            measured_at=base,
        ),
        ModelEvaluation(
            model_version_id=models[0].id, task_type="runtime",
            metrics_json={"throughput_fps": 41.6, "p50_ms": 38.4, "p95_ms": 92.7,
                          "run_id": "run-2026-06-12-b", "split": "benchmark", "status": "completed"},
            hardware="Intel Xeon W-1350 (CPU)", input_size="640x640", batch_size=1,
            measured_at=base + timedelta(hours=2),
        ),
        ModelEvaluation(
            model_version_id=models[1].id, task_type="object_detection",
            metrics_json={"mAP50": 0.907, "precision": 0.924, "recall": 0.889, "f1": 0.906,
                          "run_id": "run-2025-11-02-a", "split": "validation", "status": "completed"},
            hardware="Intel Xeon W-1350 (CPU)", input_size="640x640", batch_size=8,
            measured_at=base - timedelta(days=2),
        ),
        ModelEvaluation(
            model_version_id=models[2].id, task_type="anomaly_image",
            metrics_json={"auroc": 0.971, "image_f1": 0.933, "pixel_auroc": 0.954,
                          "run_id": "run-2026-04-18-a", "split": "validation", "status": "completed"},
            hardware="Intel Xeon W-1350 (CPU)", input_size="256x256", batch_size=4,
            measured_at=base + timedelta(days=1),
        ),
        ModelEvaluation(
            model_version_id=models[3].id, task_type="anomaly_image",
            metrics_json={"auroc": 0.918, "image_f1": 0.884, "pixel_auroc": 0.902,
                          "run_id": "run-2025-09-30-a", "split": "validation", "status": "completed"},
            hardware="Intel Xeon W-1350 (CPU)", input_size="256x256", batch_size=4,
            measured_at=base - timedelta(days=1),
        ),
    ]
    db.add_all(evaluations)
    db.flush()
    return models


def _part_box(rng: random.Random) -> tuple[float, float, float, float]:
    width = rng.uniform(34, 95)
    height = rng.uniform(28, 74)
    x1 = rng.uniform(130, 640 - 130 - width)
    y1 = rng.uniform(95, 480 - 115 - height)
    return (round(x1, 1), round(y1, 1), round(x1 + width, 1), round(y1 + height, 1))


def _detection_payload(
    rng: random.Random, target: str, rules: DecisionRules, classes: Sequence[str]
) -> tuple[list[dict], list[tuple[float, float, float, float]]]:
    """Build detection rows whose decide_inspection outcome matches ``target``."""
    detections: list[dict] = []
    boxes: list[tuple[float, float, float, float]] = []
    non_reject = [c for c in classes if c not in rules.reject_labels] or ["wear"]

    if target == Decision.FAIL:
        for _ in range(rng.randint(1, 2)):
            box = _part_box(rng)
            boxes.append(box)
            detections.append({
                "label": rng.choice(sorted(rules.reject_labels) or REJECT_LABELS),
                "confidence": round(rng.uniform(rules.detection_confidence + 0.05, 0.97), 3),
                "bbox": {"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]},
            })
    elif target in (Decision.PASS, Decision.REVIEW):
        if rng.random() < 0.55:
            for _ in range(rng.randint(1, 2)):
                box = _part_box(rng)
                detections.append({
                    "label": rng.choice(non_reject),
                    "confidence": round(rng.uniform(0.27, max(0.28, rules.detection_confidence - 0.01)), 3),
                    "bbox": {"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]},
                })
    return detections, boxes


def _anomaly_score_for(rng: random.Random, target: str, rules: DecisionRules) -> float:
    threshold = rules.anomaly_threshold if rules.anomaly_threshold is not None else 0.6
    if target == Decision.FAIL:
        return round(rng.uniform(threshold, min(1.0, threshold + 0.3)), 4)
    if target == Decision.REVIEW:
        return round(rng.uniform(max(0.0, threshold - rules.review_band), max(0.01, threshold - 0.005)), 4)
    return round(rng.uniform(0.04, max(0.05, threshold - rules.review_band - 0.02)), 4)


def seed_demo_data(
    db: Session,
    *,
    inspections: int = 240,
    hours: float = 72.0,
    with_evidence: bool = True,
    rng: random.Random | None = None,
    storage: EvidenceStorage | None = None,
) -> dict:
    """Seed a complete demo dataset. Assumes an empty (or user/role-only) database."""
    rng = rng or random.Random()
    storage = storage or EvidenceStorage()
    now = datetime.now(timezone.utc)

    roles = _ensure_roles(db)
    _seed_users(db, roles, rng)
    cameras = _seed_lines_and_cameras(db, rng)
    models = _seed_models(db, rng)
    db.flush()

    det_model = next(m for m in models if m.task_type == "object_detection" and m.is_active)
    anomaly_model = next(m for m in models if (m.task_type or "").startswith("anomaly") and m.is_active)
    det_rules = _rules_for_model(det_model)
    anomaly_rules = _rules_for_model(anomaly_model)

    # Cameras 1-2 and the RTSP/degraded/webcam ones use the detector; the
    # coating/assembly simulated cams use the anomaly model.
    station_plan: list[tuple[Camera, ModelVersion, DecisionRules]] = [
        (cameras[0], det_model, det_rules),
        (cameras[1], det_model, det_rules),
        (cameras[2], anomaly_model, anomaly_rules),
        (cameras[3], anomaly_model, anomaly_rules),
        (cameras[4], det_model, det_rules),
    ]
    active_stations = [entry for entry in station_plan if entry[0].is_active]

    decision_mix: dict[str, list[str]] = {
        "object_detection": [Decision.PASS] * 82 + [Decision.FAIL] * 13 + [Decision.ERROR] * 5,
        "anomaly": [Decision.PASS] * 70 + [Decision.REVIEW] * 14 + [Decision.FAIL] * 12 + [Decision.ERROR] * 4,
    }

    records: list[dict] = []
    for index in range(inspections):
        camera, model, rules = active_stations[index % len(active_stations)]
        if index < 6:
            # Guarantee a handful of very fresh rows so the recent tape looks live.
            offset_seconds = rng.uniform(20, 420)
        else:
            offset_seconds = hours * 3600 * (rng.random() ** 1.7)
        created = now - timedelta(seconds=offset_seconds)

        task = "anomaly" if (model.task_type or "").startswith("anomaly") else "object_detection"
        target = rng.choice(decision_mix[task])

        detections: list[dict] = []
        anomaly_score: float | None = None
        defect_boxes: list[tuple[float, float, float, float]] = []
        decision: dict
        error_status: str | None = None
        error_message: str | None = None

        if target == Decision.ERROR:
            decision = {"decision": Decision.ERROR, "reason": "Inspection could not be completed"}
            error_status, error_message = rng.choice(ERROR_PROFILES)
        elif task == "anomaly":
            anomaly_score = _anomaly_score_for(rng, target, rules)
            if target in (Decision.FAIL, Decision.REVIEW):
                defect_boxes = [_part_box(rng) for _ in range(rng.randint(1, 2))]
            decision = decide_inspection([], anomaly_score=anomaly_score, rules=rules)
        else:
            detections, defect_boxes = _detection_payload(rng, target, rules, model.classes_json or DETECTION_CLASSES)
            decision = decide_inspection(detections, anomaly_score=None, rules=rules)

        records.append({
            "camera": camera,
            "model": model,
            "created": created,
            "detections": detections,
            "anomaly_score": anomaly_score,
            "defect_boxes": defect_boxes,
            "decision": decision,
            "error_status": error_status,
            "error_message": error_message,
            "is_anomaly": task == "anomaly",
        })

    records.sort(key=lambda row: row["created"])
    counts = {"inspections": 0, "detections": 0, "anomalies": 0, "evidence": 0, "errors": 0}

    for chunk_start in range(0, len(records), 50):
        chunk = records[chunk_start:chunk_start + 50]
        for record in chunk:
            camera: Camera = record["camera"]
            model: ModelVersion = record["model"]
            created: datetime = record["created"]
            decision: dict = record["decision"]

            session = InspectionSession(
                camera_id=camera.id, model_version_id=model.id,
                start_time=created, end_time=created + timedelta(milliseconds=180),
                status="error" if decision["decision"] == Decision.ERROR else "completed",
                created_at=created,
            )
            db.add(session)
            db.flush()

            inspection = Inspection(
                session_id=session.id,
                camera_id=camera.id,
                product_id=rng.choice(PRODUCT_CODES),
                batch_id=f"BATCH-{created:%Y%m%d}-L{camera.line_id or 1}",
                model_name=model.name,
                model_version=model.framework_version,
                decision=decision["decision"],
                latency_ms=None if decision["decision"] == Decision.ERROR else round(
                    rng.uniform(25, 95) if record["is_anomaly"] else rng.uniform(38, 130), 2
                ),
                error_status=record["error_status"],
                error_message=record["error_message"],
                metadata_json={"decision_reason": decision["reason"]},
                created_at=created,
            )
            db.add(inspection)
            db.flush()

            if decision["decision"] != Decision.ERROR:
                for detection in record["detections"]:
                    bbox = detection["bbox"]
                    db.add(DetectionResult(
                        inspection_id=inspection.id,
                        label=detection["label"],
                        confidence=detection["confidence"],
                        bbox_json=bbox,
                        area=(bbox["x2"] - bbox["x1"]) * (bbox["y2"] - bbox["y1"]),
                        created_at=created,
                    ))
                    counts["detections"] += 1
                if record["anomaly_score"] is not None:
                    db.add(AnomalyResult(
                        inspection_id=inspection.id,
                        score=record["anomaly_score"],
                        threshold=record["model"].config_json.get("anomaly_threshold")
                        if record["model"].config_json else None,
                        created_at=created,
                    ))
                    counts["anomalies"] += 1
                inspection.detection_results_json = {"detections": record["detections"]}
                inspection.anomaly_score_json = (
                    {"score": record["anomaly_score"]} if record["anomaly_score"] is not None else None
                )
                if with_evidence:
                    content = render_part_image(record["defect_boxes"], rng=rng)
                    inspection.evidence_path = storage.save_image(inspection.id, content, ".jpg")
                    counts["evidence"] += 1
            else:
                counts["errors"] += 1

            camera.last_inspection_at = _later(camera.last_inspection_at, created)
            if decision["decision"] == Decision.FAIL and rng.random() < 0.35:
                db.add(SystemEvent(
                    event_type="inspection.fail", level="warning",
                    message=f"Inspection INS-{inspection.id:06d} rejected on {camera.name}",
                    metadata_json={"inspection_id": inspection.id, "camera_id": camera.id},
                    created_at=created,
                ))
            counts["inspections"] += 1
        db.commit()

    for camera in cameras:
        if camera.is_active and camera.status == CameraStatus.CONNECTED:
            db.add(SystemEvent(
                event_type="camera.connected", level="info",
                message=f"{camera.name} ({camera.camera_identity}) reported connected",
                metadata_json={"camera_id": camera.id},
            ))
    for model in models:
        if model.is_active:
            db.add(SystemEvent(
                event_type="model.activated", level="info",
                message=f"{model.name} {model.version} activated for {model.task_type}",
                metadata_json={"model_version_id": model.id},
            ))
    db.add(SystemEvent(
        event_type="system.startup", level="info",
        message="Demo dataset provisioned by app.seed",
    ))

    for user in db.query(User).all():
        for _ in range(rng.randint(2, 5)):
            login_at = now - timedelta(hours=rng.uniform(0.5, hours))
            db.add(AuditLog(
                user_id=user.id, action="auth.login",
                details_json={"username": user.username},
                ip_address=f"192.168.1.{rng.randint(20, 200)}",
                created_at=login_at,
            ))
        if user.role.name in (RoleNames.ADMIN, RoleNames.AI_ENGINEER):
            db.add(AuditLog(
                user_id=user.id, action="model.activate",
                details_json={"model": det_model.name, "version": det_model.version},
                ip_address="192.168.1.24",
                created_at=now - timedelta(hours=rng.uniform(10, hours)),
            ))
    db.commit()
    return counts


def reset_database(db: Session, storage: EvidenceStorage | None = None) -> None:
    """Delete every row and all stored evidence images. Demo/dev use only."""
    storage = storage or EvidenceStorage()
    for model in (
        AuditLog, SystemEvent, AnomalyResult, DetectionResult, Inspection,
        InspectionSession, ModelEvaluation, ModelVersion, Camera, ProductionLine,
        User, Role,
    ):
        db.query(model).delete(synchronize_session=False)
    db.commit()
    if storage.root.name == "evidence" and storage.root.exists():
        shutil.rmtree(storage.root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="industrialvision-seed", description="Seed demo data")
    parser.add_argument("--reset", action="store_true", help="Wipe all data before seeding")
    parser.add_argument(
        "--confirm-reset", default="", metavar="TOKEN",
        help=f"Required with --reset; must equal {RESET_CONFIRMATION}",
    )
    parser.add_argument("--append", action="store_true", help="Add inspections even if data exists")
    parser.add_argument("--inspections", type=int, default=240, help="Number of inspection records")
    parser.add_argument("--hours", type=float, default=72.0, help="Spread inspections over the last N hours")
    parser.add_argument("--no-evidence", action="store_true", help="Skip generating evidence images")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible data")
    args = parser.parse_args()

    if settings.APP_ENV.casefold() == "production":
        parser.error("app.seed is disabled when APP_ENV=production; it writes fabricated demo data.")
    if args.reset and args.confirm_reset != RESET_CONFIRMATION:
        parser.error(
            "--reset deletes every table including user accounts. "
            f"Re-run with --confirm-reset {RESET_CONFIRMATION}"
        )

    rng = random.Random(args.seed)
    db = SessionLocal()
    try:
        existing = db.query(Inspection).count()
        if args.reset:
            # Wipe unconditionally: residue in other tables (old cameras,
            # users, models) must not survive a demo reset.
            reset_database(db)
            existing = 0
            print("Existing data wiped.")
        if existing and not args.append:
            parser.error(
                f"Database already contains {existing} inspections. "
                "Use --reset to start over or --append to add more."
            )
        summary = seed_demo_data(
            db,
            inspections=max(1, args.inspections),
            hours=max(0.1, args.hours),
            with_evidence=not args.no_evidence,
            rng=rng,
        )
        print(f"Seeded {summary['inspections']} inspections "
              f"({summary['detections']} detections, {summary['anomalies']} anomaly scores, "
              f"{summary['evidence']} evidence images, {summary['errors']} simulated errors).")
        print("Demo accounts: admin/Admin!Vision2026 · operator1/Operator!2026 · aiengineer/Engineer!2026")
    finally:
        db.close()


if __name__ == "__main__":
    main()
