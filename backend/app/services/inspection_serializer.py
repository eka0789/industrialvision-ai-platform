"""Shared ORM-to-dict serialization for inspections.

Used by the REST router, the WebSocket live feed, and the demo seeder so every
surface emits an identical payload shape.
"""

from __future__ import annotations

from typing import Any

from app.db.models import Inspection


def serialize_inspection(inspection: Inspection) -> dict[str, Any]:
    """Serialize an Inspection ORM row (with detections/anomaly loaded) to a dict."""
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
