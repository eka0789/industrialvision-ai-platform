"""Inspection lifecycle and persistence for successful/failed model inference."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from app.db.models import Camera, Inspection, InspectionSession, ModelVersion
from app.db.session import SessionLocal
from app.services.decision import DecisionRules, decide_inspection
from app.services.inference import predict_frame


class InspectionManager:
    """Create and close inspection sessions using the configured database."""

    def start_inspection(
        self,
        camera_id: int,
        model_version_id: int,
        product_id: str | None = None,
        batch_id: str | None = None,
    ) -> Inspection:
        db = SessionLocal()
        try:
            camera = db.get(Camera, camera_id)
            model = db.get(ModelVersion, model_version_id)
            if camera is None:
                raise ValueError(f"Camera {camera_id} not found")
            if model is None:
                raise ValueError(f"Model version {model_version_id} not found")

            session = InspectionSession(camera_id=camera_id, model_version_id=model_version_id)
            db.add(session)
            db.flush()
            inspection = Inspection(
                session_id=session.id,
                camera_id=camera_id,
                product_id=product_id,
                batch_id=batch_id,
                model_name=model.name,
                model_version=model.framework_version,
                decision="ERROR",
                error_status="awaiting_inference",
                error_message="No inference result has been recorded yet",
            )
            db.add(inspection)
            db.commit()
            db.refresh(inspection)
            return inspection
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def end_inspection(self, inspection_id: int, final_score: float | None = None) -> Inspection | None:
        del final_score  # No calibrated aggregate-score policy is implemented.
        db = SessionLocal()
        try:
            inspection = db.get(Inspection, inspection_id)
            if inspection is None:
                return None
            session = db.get(InspectionSession, inspection.session_id)
            if session is not None and session.end_time is None:
                session.end_time = datetime.now(timezone.utc)
                session.status = "completed"
            db.commit()
            db.refresh(inspection)
            return inspection
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


_inspection_mgr = InspectionManager()


def get_inspection_mgr() -> InspectionManager:
    return _inspection_mgr


def start_inspection_session(
    camera_id: int,
    model_version_id: int,
    product_id: str | None = None,
    batch_id: str | None = None,
) -> Inspection:
    return _inspection_mgr.start_inspection(camera_id, model_version_id, product_id, batch_id)


def end_inspection_session(inspection_id: int, final_score: float | None = None) -> Inspection | None:
    return _inspection_mgr.end_inspection(inspection_id, final_score)


def _rules_for_model(model: ModelVersion | None) -> DecisionRules:
    config = (model.config_json or {}) if model is not None else {}
    labels = config.get("reject_labels", [])
    if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise ValueError("Model reject_labels must be a list of strings")
    return DecisionRules(
        reject_labels=frozenset(labels),
        detection_confidence=float(config.get("detection_confidence", 0.5)),
        anomaly_threshold=(
            float(config["anomaly_threshold"])
            if config.get("anomaly_threshold") is not None
            else None
        ),
        review_band=float(config.get("review_band", 0.05)),
    )


async def process_camera_frame(
    inspection_id: int,
    frame: Any,
    websocket: WebSocket | None = None,
) -> dict[str, Any]:
    """Infer one frame and persist its actual output and fail-safe decision."""
    db = SessionLocal()
    try:
        inspection = db.get(Inspection, inspection_id)
        if inspection is None:
            return {"decision": "ERROR", "reason": "Inspection not found", "error": "not_found"}

        try:
            model = db.get(ModelVersion, inspection.session.model_version_id)
            rules = _rules_for_model(model)
            result = predict_frame(frame)
            detections = result.get("detections")
            if not isinstance(detections, list):
                raise ValueError("Inference returned an invalid detections result")
            latency_ms = float(result["latency_ms"])
            if latency_ms < 0:
                raise ValueError("Inference returned an invalid latency")
            decision_result = decide_inspection(
                detections,
                anomaly_score=result.get("anomaly_score"),
                rules=rules,
            )
        except Exception as exc:
            decision_result = decide_inspection(None, inference_error=str(exc))
            inspection.decision = decision_result["decision"]
            inspection.error_status = "inference_failed"
            inspection.error_message = decision_result["error"]
            inspection.metadata_json = {"decision_reason": decision_result["reason"]}
            db.commit()
            response = {**decision_result, "inspection_id": inspection_id, "detections": []}
        else:
            for detection in detections:
                bbox = detection.get("bbox")
                bbox_json = None
                if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                    bbox_json = {
                        "x1": float(bbox[0]),
                        "y1": float(bbox[1]),
                        "x2": float(bbox[2]),
                        "y2": float(bbox[3]),
                    }
                from app.db.models import DetectionResult

                db.add(
                    DetectionResult(
                        inspection_id=inspection_id,
                        label=str(detection.get("label", "unknown")),
                        confidence=float(detection.get("confidence", 0.0)),
                        bbox_json=bbox_json,
                    )
                )

            inspection.detection_results_json = {"detections": detections}
            inspection.latency_ms = latency_ms
            inspection.decision = decision_result["decision"]
            is_decision_error = decision_result["decision"] == "ERROR"
            inspection.error_status = "decision_error" if is_decision_error else None
            inspection.error_message = (
                decision_result.get("error") or decision_result["reason"]
                if is_decision_error
                else None
            )
            inspection.metadata_json = {"decision_reason": decision_result["reason"]}
            db.commit()
            response = {
                **decision_result,
                "inspection_id": inspection_id,
                "detections": detections,
                "latency_ms": latency_ms,
                "anomaly_score": result.get("anomaly_score"),
            }

        if websocket is not None:
            try:
                await websocket.send_json(response)
            except Exception:
                pass
        return response
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
