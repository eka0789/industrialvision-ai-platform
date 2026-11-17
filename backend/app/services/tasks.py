"""Registered background task functions.

Every function here is executable by both task-queue transports (in-process
pool and the standalone RQ worker) — the same code runs regardless of where it
is scheduled. Tasks must be importable module-level functions with
JSON-serializable arguments (they cross the Redis queue in worker mode).
"""

from __future__ import annotations

import logging

from app.services.task_queue import register_task

logger = logging.getLogger(__name__)


@register_task("video_inspection")
def run_video_inspection(camera_id: int, file_path: str, frame_interval: int = 1, max_frames: int | None = None) -> dict:
    """Inspect a video file for a camera and return the aggregated result dict.

    Loads the camera fresh from the database (queue arguments must be
    serializable primitives). Returns the same payload shape the synchronous
    ``POST /cameras/{id}/inspect-video`` endpoint returns so callers can treat
    both paths identically.
    """
    from dataclasses import asdict

    from app.db.models import Camera
    from app.db.session import SessionLocal
    from app.services.video_inspection import inspect_video_file

    db = SessionLocal()
    try:
        camera = db.get(Camera, camera_id)
        if camera is None:
            raise ValueError(f"Camera {camera_id} not found for video_inspection task")
        result = inspect_video_file(camera, file_path, frame_interval=frame_interval, max_frames=max_frames)
        return asdict(result)
    finally:
        db.close()


@register_task("rag_ingest_document")
def run_rag_ingestion(document_id: int) -> dict:
    """Extract → chunk → embed → store for one uploaded document (all-or-nothing)."""
    from app.db.session import SessionLocal
    from app.rag.ingestion import ingest_document

    db = SessionLocal()
    try:
        return ingest_document(db, document_id)
    finally:
        db.close()


@register_task("pdm_prune_sensor_readings")
def run_pdm_prune() -> dict:
    """Delete sensor readings older than the retention window (B1 policy)."""
    from datetime import datetime, timedelta, timezone

    from app.core.config import settings
    from app.db.models import SensorReading
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.PDM_SENSOR_RETENTION_DAYS)
        deleted = (
            db.query(SensorReading)
            .filter(SensorReading.recorded_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"deleted": int(deleted), "cutoff": cutoff.isoformat()}
    finally:
        db.close()


@register_task("pdm_drift_check")
def run_drift_check(org_id: int = 1, model_id: int | None = None) -> dict:
    """Scheduled PSI drift check; pushes the worst feature PSI to Prometheus.

    Missing reference stats are an explicit per-model error recorded in the
    result — never treated as zero drift.
    """
    from app.db.models import ModelVersion
    from app.db.session import SessionLocal
    from app.services.drift import drift_report
    from app.services.metrics import MODEL_DRIFT_PSI

    db = SessionLocal()
    try:
        query = db.query(ModelVersion).filter(ModelVersion.org_id == org_id)
        if model_id:
            query = query.filter(ModelVersion.id == model_id)
        results = []
        for model in query.all():
            try:
                report = drift_report(db, org_id=org_id, model_id=model.id)
            except ValueError as exc:
                results.append({"model_id": model.id, "error": str(exc)})
                continue
            results.append({"model_id": model.id, "overall_psi": report["overall_psi"], "drifted": report["drifted"]})
            for feature, data in report["features"].items():
                if isinstance(data, dict) and "psi" in data:
                    MODEL_DRIFT_PSI.labels(model=model.name, feature=feature).set(data["psi"])
        return {"org_id": org_id, "models": results}
    finally:
        db.close()


@register_task("generate_report_file")
def run_generate_report(org_id: int, period: str, fmt: str, payload: dict) -> dict:
    """Render a report payload to JSON/CSV on the worker (F4)."""
    from app.api.v1.routers.reports import write_report_file

    name = write_report_file(org_id, period, fmt, payload)
    return {"filename": name, "period": period, "format": fmt}


@register_task("evidence_prune_retention")
def run_evidence_prune(pass_retention_days: int | None = None, audit_retention_days: int | None = None) -> dict:
    """Prune expired PASS evidence (7 days) while retaining FAIL/REVIEW audit evidence (1-2 years)."""
    from app.db.session import SessionLocal
    from app.services.evidence import prune_expired_evidence

    db = SessionLocal()
    try:
        return prune_expired_evidence(
            db,
            pass_retention_days=pass_retention_days,
            audit_retention_days=audit_retention_days,
        )
    finally:
        db.close()
