"""Image inspection and database-backed inspection history routes."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Annotated, Any, Literal
import warnings

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from starlette.concurrency import run_in_threadpool

from app.api.v1.deps import require_roles
from app.core.config import settings
from app.core.constants import Role
from app.core.logging_utils import redact
from app.db.models import (
    AnomalyResult,
    Camera,
    DetectionResult,
    Inspection,
    InspectionSession,
    ModelVersion,
    User,
)
from app.db.session import get_db
from app.schemas.inspection import InspectionOut, InspectionPage
from app.services.decision import decide_inspection
from app.services.evidence import EvidenceStorage
from app.services.inspection_manager import _rules_for_model
from app.vision.contracts import InferenceResult
from app.vision.factory import create_detection_engine

router = APIRouter(prefix="/inspections", tags=["inspections"])

_IMAGE_TYPES = {
    "image/jpeg": (".jpg", "JPEG", lambda content: content.startswith(b"\xff\xd8\xff")),
    "image/png": (".png", "PNG", lambda content: content.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": (
        ".webp",
        "WEBP",
        lambda content: len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP",
    ),
}


def _serialize_inspection(inspection: Inspection) -> dict[str, Any]:
    detections = [
        {
            "label": row.label,
            "confidence": row.confidence,
            "bbox": row.bbox_json,
            "area": row.area,
        }
        for row in inspection.detections
    ]
    anomaly = None
    if inspection.anomaly is not None:
        anomaly = {
            "score": inspection.anomaly.score,
            "heatmap_path": inspection.anomaly.heatmap_path,
            "threshold": inspection.anomaly.threshold,
        }
    metadata = inspection.metadata_json or {}
    return {
        "id": inspection.id,
        "timestamp": inspection.created_at,
        "camera_id": inspection.camera_id,
        "session_id": inspection.session_id,
        "product_id": inspection.product_id,
        "batch_id": inspection.batch_id,
        "model_name": inspection.model_name,
        "model_version": inspection.model_version,
        "detections": detections,
        "anomaly": anomaly,
        "decision": inspection.decision,
        "decision_reason": metadata.get("decision_reason"),
        "latency_ms": inspection.latency_ms,
        "evidence_path": inspection.evidence_path,
        "error_status": inspection.error_status,
        "error_message": inspection.error_message,
        "metadata": metadata,
    }


def _decode_uploaded_image(upload: UploadFile, content: bytes):
    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    signature = _IMAGE_TYPES.get(content_type)
    if signature is None or not signature[2](content):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload must be a valid JPEG, PNG, or WebP image",
        )
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Pillow is required to decode image uploads",
        ) from exc

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.format != signature[1]:
                    raise ValueError("Image encoding does not match Content-Type")
                width, height = source.size
                if width <= 0 or height <= 0 or width * height > settings.MAX_IMAGE_PIXELS:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Image exceeds the maximum pixel count",
                    )
                source.verify()
            with Image.open(BytesIO(content)) as source:
                rgb = np.asarray(source.convert("RGB"), dtype=np.uint8)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Image could not be decoded",
        ) from exc
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    return bgr, signature[0]


def _finish_error(
    db: Session,
    inspection: Inspection,
    inspection_session: InspectionSession,
    camera: Camera,
    error_status: str,
    message: str,
) -> InspectionOut:
    safe_message = str(redact(message))[:1000]
    inspection.decision = "ERROR"
    inspection.error_status = error_status
    inspection.error_message = safe_message
    inspection.metadata_json = {"decision_reason": "Inspection could not be completed"}
    inspection_session.status = "error"
    inspection_session.end_time = datetime.now(timezone.utc)
    camera.last_error = safe_message
    db.commit()
    db.refresh(inspection)
    return InspectionOut.model_validate(_serialize_inspection(inspection))


@router.post("/image", response_model=InspectionOut, status_code=status.HTTP_201_CREATED)
async def inspect_image(
    file: Annotated[UploadFile, File(...)],
    camera_id: Annotated[int, Form(gt=0)],
    model_version_id: Annotated[int, Form(gt=0)],
    product_id: Annotated[str | None, Form(max_length=200)] = None,
    batch_id: Annotated[str | None, Form(max_length=200)] = None,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER)),
) -> InspectionOut:
    content = await file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Image exceeds upload limit")
    image, extension = _decode_uploaded_image(file, content)

    camera = db.get(Camera, camera_id)
    model = db.get(ModelVersion, model_version_id)
    if camera is None or not camera.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active camera not found")
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model version not found")

    inspection_session = InspectionSession(camera_id=camera.id, model_version_id=model.id)
    db.add(inspection_session)
    db.flush()
    inspection = Inspection(
        session_id=inspection_session.id,
        camera_id=camera.id,
        product_id=product_id,
        batch_id=batch_id,
        model_name=model.name,
        model_version=model.framework_version,
        decision="ERROR",
        error_status="inference_not_started",
        error_message="Inspection has not completed",
    )
    db.add(inspection)
    db.commit()
    db.refresh(inspection)

    try:
        evidence_path = EvidenceStorage().save_image(inspection.id, content, extension)
        inspection.evidence_path = evidence_path
        db.commit()
    except Exception as exc:
        db.rollback()
        inspection = db.get(Inspection, inspection.id)
        inspection_session = db.get(InspectionSession, inspection_session.id)
        camera = db.get(Camera, camera_id)
        return _finish_error(db, inspection, inspection_session, camera, "evidence_storage_failed", str(exc))

    try:
        engine = create_detection_engine(model)
        result = await run_in_threadpool(engine.predict, image)
        if not isinstance(result, InferenceResult):
            result = InferenceResult.model_validate(result)
        detection_data = [detection.model_dump(mode="json") for detection in result.detections]
        anomaly_score = result.anomaly.score if result.anomaly is not None else None
        decision_result = decide_inspection(
            detection_data,
            anomaly_score=anomaly_score,
            rules=_rules_for_model(model),
        )

        for detection in result.detections:
            x1, y1, x2, y2 = detection.bbox
            db.add(
                DetectionResult(
                    inspection_id=inspection.id,
                    label=detection.label,
                    confidence=detection.confidence,
                    bbox_json={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    area=(x2 - x1) * (y2 - y1),
                )
            )
        if result.anomaly is not None:
            db.add(
                AnomalyResult(
                    inspection_id=inspection.id,
                    score=result.anomaly.score,
                    threshold=(model.config_json or {}).get("anomaly_threshold"),
                )
            )

        inspection.detection_results_json = {"detections": detection_data}
        inspection.anomaly_score_json = (
            {"score": result.anomaly.score}
            if result.anomaly is not None
            else None
        )
        inspection.decision = decision_result["decision"]
        inspection.latency_ms = result.latency_ms
        inspection.error_status = "decision_error" if inspection.decision == "ERROR" else None
        inspection.error_message = decision_result.get("error") or decision_result["reason"] if inspection.decision == "ERROR" else None
        inspection.metadata_json = {"decision_reason": decision_result["reason"]}
        inspection_session.status = "completed"
        inspection_session.end_time = datetime.now(timezone.utc)
        camera.last_inspection_at = inspection_session.end_time
        camera.last_error = None
        db.commit()
        db.refresh(inspection)
        return InspectionOut.model_validate(_serialize_inspection(inspection))
    except Exception as exc:
        db.rollback()
        inspection = db.get(Inspection, inspection.id)
        inspection_session = db.get(InspectionSession, inspection_session.id)
        camera = db.get(Camera, camera_id)
        return _finish_error(db, inspection, inspection_session, camera, "inference_failed", str(exc))


@router.get("/{inspection_id}/evidence", response_class=FileResponse)
def get_inspection_evidence(
    inspection_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER)),
) -> FileResponse:
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection not found")
    if not inspection.evidence_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection evidence not found")
    try:
        evidence = EvidenceStorage().resolve(inspection.evidence_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection evidence not found") from exc
    media_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    return FileResponse(
        evidence,
        media_type=media_types.get(evidence.suffix.casefold(), "application/octet-stream"),
        filename=f"inspection-{inspection.id}{evidence.suffix.casefold()}",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.get("", response_model=InspectionPage)
def list_inspections(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    decision: Literal["PASS", "FAIL", "REVIEW", "ERROR"] | None = None,
    camera_id: int | None = Query(default=None, gt=0),
    batch_id: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER)),
) -> InspectionPage:
    query = db.query(Inspection)
    if decision is not None:
        query = query.filter(Inspection.decision == decision)
    if camera_id is not None:
        query = query.filter(Inspection.camera_id == camera_id)
    if batch_id is not None:
        query = query.filter(Inspection.batch_id == batch_id)

    total = query.with_entities(func.count(Inspection.id)).scalar() or 0
    rows = (
        query.options(selectinload(Inspection.detections), selectinload(Inspection.anomaly))
        .order_by(Inspection.created_at.desc(), Inspection.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return InspectionPage(
        items=[InspectionOut.model_validate(_serialize_inspection(row)) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
    )


@router.get("/{inspection_id}", response_model=InspectionOut)
def get_inspection(
    inspection_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER)),
) -> InspectionOut:
    inspection = (
        db.query(Inspection)
        .options(selectinload(Inspection.detections), selectinload(Inspection.anomaly))
        .filter(Inspection.id == inspection_id)
        .first()
    )
    if inspection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection not found")
    return InspectionOut.model_validate(_serialize_inspection(inspection))
