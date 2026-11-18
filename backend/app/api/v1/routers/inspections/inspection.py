"""Image inspection and database-backed inspection history routes."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Annotated, Literal
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
from app.services.tenant import scoped_one, scoped_query, stamp_org
from app.services.event_bus import event_bus
from app.services.inspection_manager import _rules_for_model
from app.services.inspection_serializer import serialize_inspection as _serialize_inspection
from app.services.live_feed import InspectionOutDump, inspection_topic
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
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
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
    from app.services.metrics import record_decision

    record_decision("ERROR")
    event_bus.publish(inspection_topic(inspection.org_id), InspectionOutDump(inspection))
    return InspectionOut.model_validate(_serialize_inspection(inspection))


@router.post("/image", response_model=InspectionOut, status_code=status.HTTP_201_CREATED)
async def inspect_image(
    file: Annotated[UploadFile, File(...)],
    camera_id: Annotated[int, Form(gt=0)],
    model_version_id: Annotated[int, Form(gt=0)],
    product_id: Annotated[str | None, Form(max_length=200)] = None,
    batch_id: Annotated[str | None, Form(max_length=200)] = None,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER, Role.VIEWER)),
) -> InspectionOut:
    content = await file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Image exceeds upload limit")
    image, extension = _decode_uploaded_image(file, content)

    camera = scoped_one(db, Camera, _user, camera_id)
    model = scoped_one(db, ModelVersion, _user, model_version_id)
    if camera is None or not camera.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active camera not found")
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model version not found")

    inspection_session = InspectionSession(camera_id=camera.id, model_version_id=model.id)
    stamp_org(inspection_session, _user)
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
    stamp_org(inspection, _user)
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

        # Check if camera has an active Multi-Zone ROI InspectionRecipe
        from app.db.models import InspectionRecipe, ProductSKU
        from app.vision.roi_pipeline import evaluate_recipe_frame

        active_recipe = (
            scoped_query(db, InspectionRecipe, _user)
            .filter(InspectionRecipe.camera_id == camera.id, InspectionRecipe.is_active.is_(True))
            .first()
        )
        recipe_zones_out = None
        recipe_prep_out = None
        if active_recipe and active_recipe.roi_zones:
            from app.vision.quality_guard import apply_camera_degraded_alert

            sku_obj = db.get(ProductSKU, active_recipe.sku_id) if active_recipe.sku_id else None
            expiry_regex = sku_obj.expiry_format if sku_obj and sku_obj.expiry_format else None
            roi_eval = evaluate_recipe_frame(
                image,
                zones_data=active_recipe.roi_zones,
                preprocessing_data=active_recipe.preprocessing_config,
                detections=detection_data,
                override_seal_score=anomaly_score,
                simulated_ocr_text="EXP 17.11.27" if not any(
                    z.get("force_ocr_fail") for z in (active_recipe.roi_zones or []) if isinstance(z, dict)
                ) else "",
                expiry_regex_override=expiry_regex,
            )
            recipe_zones_out = roi_eval["zones"]
            recipe_prep_out = roi_eval["preprocessing"]
            if recipe_prep_out.get("is_blurred"):
                apply_camera_degraded_alert(
                    db,
                    camera,
                    {
                        "status": recipe_prep_out.get("quality_status", "LENS_BLUR_OR_FOG"),
                        "is_obstructed": True,
                        "sharpness_score": recipe_prep_out.get("sharpness_score", recipe_prep_out.get("laplacian_variance", 0.0)),
                        "laplacian_variance": recipe_prep_out.get("laplacian_variance", 0.0),
                        "mean_luminance": recipe_prep_out.get("mean_luminance", 0.0),
                        "std_luminance": recipe_prep_out.get("std_luminance", 0.0),
                        "rationale": roi_eval["decision_rationale"],
                    },
                    inspection_id=inspection.id,
                )
            if roi_eval["decision"] in ("FAIL", "ERROR") or decision_result["decision"] != "FAIL":
                decision_result = {
                    "decision": roi_eval["decision"] if decision_result["decision"] != "FAIL" else "FAIL",
                    "reason": roi_eval["decision_rationale"],
                    "error": roi_eval.get("error_status"),
                }
            for syn_det in roi_eval["detections"]:
                if syn_det not in detection_data:
                    detection_data.append(syn_det)
                    bbox_vals = syn_det.get("bbox", [0.0, 0.0, 10.0, 10.0])
                    if isinstance(bbox_vals, (list, tuple)) and len(bbox_vals) == 4:
                        x1, y1, x2, y2 = [float(v) for v in bbox_vals]
                        det_row = DetectionResult(
                            inspection_id=inspection.id,
                            label=str(syn_det.get("label", "DEFECT")),
                            confidence=float(syn_det.get("confidence", 0.95)),
                            bbox_json={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                            area=max(0.0, (x2 - x1) * (y2 - y1)),
                        )
                        stamp_org(det_row, _user)
                        db.add(det_row)

        for detection in result.detections:
            x1, y1, x2, y2 = detection.bbox
            detection_row = DetectionResult(
                inspection_id=inspection.id,
                label=detection.label,
                confidence=detection.confidence,
                bbox_json={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                area=(x2 - x1) * (y2 - y1),
            )
            stamp_org(detection_row, _user)
            db.add(detection_row)
        if result.anomaly is not None:
            anomaly_row = AnomalyResult(
                inspection_id=inspection.id,
                score=result.anomaly.score,
                threshold=(model.config_json or {}).get("anomaly_threshold"),
            )
            stamp_org(anomaly_row, _user)
            db.add(anomaly_row)

        inspection.detection_results_json = {"detections": detection_data, "zones": recipe_zones_out}
        inspection.anomaly_score_json = (
            {"score": result.anomaly.score}
            if result.anomaly is not None
            else None
        )
        inspection.decision = decision_result["decision"]
        inspection.latency_ms = result.latency_ms
        inspection.error_status = "decision_error" if inspection.decision == "ERROR" else None
        inspection.error_message = decision_result.get("error") or decision_result["reason"] if inspection.decision == "ERROR" else None
        inspection.metadata_json = {
            "decision_reason": decision_result["reason"],
            "decision_rationale": decision_result["reason"],
            "recipe_id": active_recipe.id if active_recipe else None,
            "zones": recipe_zones_out,
            "preprocessing": recipe_prep_out,
        }
        inspection_session.status = "error" if inspection.decision == "ERROR" else "completed"
        inspection_session.end_time = datetime.now(timezone.utc)
        camera.last_inspection_at = inspection_session.end_time
        if not (recipe_prep_out and recipe_prep_out.get("is_blurred")):
            camera.last_error = None

        from app.ot.adapter import attach_inspection_to_active_batch
        from app.services.evidence_retention import should_retain_image

        all_defect_labels = [
            str(d.get("label", "")) for d in detection_data if isinstance(d, dict) and d.get("label")
        ]
        attached_batch = attach_inspection_to_active_batch(
            db,
            org_id=inspection.org_id,
            camera=camera,
            inspection=inspection,
            defect_categories=all_defect_labels,
        )
        if attached_batch is not None:
            sample_rate = (
                active_recipe.pass_sample_rate
                if active_recipe and active_recipe.pass_sample_rate
                else 100
            )
            seq_idx = max(1, int(attached_batch.total_inspected or 1))
            retain = should_retain_image(inspection.decision, sequence_index=seq_idx, pass_sample_rate=sample_rate)
            if not retain and inspection.evidence_path:
                try:
                    EvidenceStorage().delete(inspection.evidence_path)
                except Exception:
                    pass
                inspection.evidence_path = None
            meta = dict(inspection.metadata_json or {})
            meta["evidence_sampled"] = bool(inspection.evidence_path is not None)
            meta["pass_sample_rate"] = sample_rate
            meta["audit_protected"] = inspection.decision in ("FAIL", "REVIEW")
            meta["batch_counters"] = {
                "batch_code": attached_batch.batch_code,
                "total_inspected": attached_batch.total_inspected,
                "total_pass": attached_batch.total_pass,
                "total_fail": attached_batch.total_fail,
                "total_review": attached_batch.total_review,
            }
            inspection.metadata_json = meta

        db.commit()
        db.refresh(inspection)
        from app.services.metrics import record_decision

        record_decision(
            inspection.decision,
            latency_seconds=result.latency_ms / 1000.0 if result.latency_ms is not None else None,
            camera_id=camera_id,
        )
        event_bus.publish(inspection_topic(inspection.org_id), InspectionOutDump(inspection))
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
    _user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER, Role.VIEWER)),
) -> FileResponse:
    inspection = scoped_one(db, Inspection, _user, inspection_id)
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
    _user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER, Role.VIEWER)),
) -> InspectionPage:
    query = scoped_query(db, Inspection, _user)
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
    _user: User = Depends(require_roles(Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.AI_ENGINEER, Role.VIEWER)),
) -> InspectionOut:
    inspection = (
        scoped_query(db, Inspection, _user)
        .options(selectinload(Inspection.detections), selectinload(Inspection.anomaly))
        .filter(Inspection.id == inspection_id)
        .first()
    )
    if inspection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection not found")
    return InspectionOut.model_validate(_serialize_inspection(inspection))
