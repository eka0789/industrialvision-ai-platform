"""Safe factory for model adapters stored below the configured model root."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.models import ModelVersion
from app.vision.contracts import DetectionEngine


def resolve_model_path(model_root: str | Path, model_path: str | Path) -> Path:
    root = Path(model_root).expanduser().resolve()
    candidate = Path(model_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("Model path must resolve inside MODEL_ROOT")
    if not resolved.is_file():
        raise ValueError("Configured model path must be a file")
    return resolved


def create_detection_engine(model: ModelVersion) -> DetectionEngine:
    if not model.source_path:
        raise ValueError("Model version has no configured source_path")
    config: dict[str, Any] = model.config_json or {}
    confidence = float(config.get("confidence_threshold", settings.CONF_THRESHOLD))
    image_size = int(config.get("image_size", settings.IMAGE_SIZE))
    device = str(config.get("device", model.device or settings.ONNX_DEVICE)).casefold()
    framework = model.framework.casefold()

    if framework in {"onnx", "onnxruntime"}:
        output_format = config.get("output_format")
        class_names = config.get("class_names")
        if output_format != "xyxy_conf_class_nx6":
            raise ValueError("ONNX models require output_format='xyxy_conf_class_nx6'")
        if not isinstance(class_names, (list, dict)) or not class_names:
            raise ValueError("ONNX models require configured class_names")
        path = resolve_model_path(settings.MODEL_ROOT, model.source_path)
        from app.vision.onnx import ONNXDetectionEngine

        return ONNXDetectionEngine(
            path,
            output_format=output_format,
            class_names=class_names,
            device=device,
            confidence_threshold=confidence,
            image_size=image_size,
        )

    if framework in {"yolo", "ultralytics"}:
        class_names = config.get("class_names")
        if not isinstance(class_names, (list, dict)) or not class_names:
            raise ValueError("YOLO models require configured class_names")
        path = resolve_model_path(settings.MODEL_ROOT, model.source_path)
        from app.vision.yolo import UltralyticsYOLOEngine

        return UltralyticsYOLOEngine(
            path,
            class_names=class_names,
            confidence_threshold=confidence,
            iou_threshold=float(config.get("iou_threshold", settings.IOU_THRESHOLD)),
            image_size=image_size,
            device=device,
        )

    raise ValueError(f"Unsupported model framework: {model.framework!r}")
