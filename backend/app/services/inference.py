"""Compatibility helpers backed by strict, model-specific inference adapters."""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.vision.contracts import InferenceResult
from app.vision.onnx import ONNXDetectionEngine

_inference: ONNXDetectionEngine | None = None


def get_inference() -> ONNXDetectionEngine:
    """Lazily load the explicitly configured default ONNX model."""
    global _inference
    if _inference is None:
        _inference = ONNXDetectionEngine(
            f"{settings.MODEL_ROOT}/{settings.DEFAULT_ONNX_MODEL}",
            output_format="xyxy_conf_class_nx6",
            class_names=(),
            device=settings.ONNX_DEVICE,
            confidence_threshold=settings.CONF_THRESHOLD,
            image_size=settings.IMAGE_SIZE,
        )
    return _inference


def predict_frame(frame: Any) -> dict[str, Any]:
    """Return the stable dictionary wire shape used by the camera service."""
    result: InferenceResult = get_inference().predict(frame)
    return {
        "detections": [detection.model_dump(mode="json") for detection in result.detections],
        "anomaly_score": result.anomaly.score if result.anomaly else None,
        "latency_ms": result.latency_ms,
    }
