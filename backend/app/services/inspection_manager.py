"""Inspection session management with real-time detection."""

from __future__ import annotations

import time
from typing import Optional, Dict, Any, List

from fastapi import WebSocket

from app.db.models import Camera, ModelVersion, InspectionSession, Inspection, DetectionResult, AnomalyResult, User
from app.db.session import SessionLocal
from app.services.inference import get_inference, predict_frame
from app.services.camera import start_camera_stream, CameraStreamError
from app.schemas.inspection import InspectionCreate, InspectionOut, DetectionOut, AnomalyOut

# Global inspection manager
_inspection_mgr = InspectionManager()


def get_inspection_mgr() -> InspectionManager:
    """Get the global inspection manager instance."""
    return _inspection_mgr


def start_inspection_session(
    camera_id: int,
    model_version_id: int,
    product_id: str | None = None,
    batch_id: str | None = None,
) -> Inspection:
    """Convenience function to start an inspection session."""
    return _inspection_mgr.start_inspection(camera_id, model_version_id, product_id, batch_id)


def end_inspection_session(inspection_id: int, final_score: float | None = None) -> Optional[Inspection]:
    """Convenience function to end an inspection session."""
    return _inspection_mgr.end_inspection(inspection_id, final_score)


class InspectionSessionContext:
    """Context for a single inspection session."""

    def __init__(
        self,
        inspection_id: int,
        camera_id: int,
        model_version_id: int,
        start_time: float,
        product_id: str | None = None,
        batch_id: str | None = None,
    ):
        self.inspection_id = inspection_id
        self.camera_id = camera_id
        self.model_version_id = model_version_id
        self.start_time = start_time
        self.product_id = product_id
        self.batch_id = batch_id


async def process_camera_frame(
    inspection_id: int,
    frame: np.ndarray,
    websocket: WebSocket | None = None,
) -> Dict[str, Any]:
    """Process a single frame through ONNX inference and update inspection."""
    from app.db.models import DetectionResult, AnomalyResult

    result = predict_frame(frame)

    db = SessionLocal()
    try:
        inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
        if not inspection:
            return {"error": "Inspection not found", "detections": []}

        # Save detection results
        for det in result.get("detections", []):
            label = det.get("label", "unknown")
            confidence = det.get("confidence", 0.0)
            bbox = det.get("bbox", [])

            dr = DetectionResult(
                inspection_id=inspection_id,
                label=label,
                confidence=confidence,
                bbox_json={"x1": bbox[0], "y1": bbox[1], "x2": bbox[2], "y3": bbox[3]}
                if len(bbox) >= 4
                else None,
                area=None,
            )
            db.add(dr)

        # Compute anomaly score (simple heuristic: avg confidence of detections)
        detections = result.get("detections", [])
        anomaly_score = float(np.mean([d.get("confidence", 0) for d in detections])) if detections else 0.0

        # Save anomaly result
        ar = AnomalyResult(
            inspection_id=inspection_id,
            score=anomaly_score,
            threshold=0.5,
        )
        db.add(ar)

        # Update inspection decision
        if anomaly_score > 0.5:
            inspection.decision = "ERROR"
        else:
            inspection.decision = "PASS"

        db.commit()
        db.refresh(inspection)

        # Send websocket update if connected
        if websocket:
            try:
                await websocket.send_json({
                    "inspection_id": inspection_id,
                    "detections": [{"label": d.label, "confidence": d.confidence, "bbox": d.bbox_json}
                                   for d in detections],
                    "anomaly_score": anomaly_score,
                    "decision": inspection.decision,
                    "latency_ms": result.get("latency_ms", 0),
                })
            except Exception:
                pass

        return {
            "detections": [{"label": d.label, "confidence": d.confidence, "bbox": d.bbox_json}
                           for d in detections],
            "anomaly_score": anomaly_score,
            "decision": inspection.decision,
            "latency_ms": result.get("latency_ms", 0),
        }
    finally:
        db.close()