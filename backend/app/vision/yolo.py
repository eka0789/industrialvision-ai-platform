"""Optional Ultralytics YOLO adapter; the package is loaded only on demand."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from app.vision.contracts import DetectionPrediction, InferenceResult


class UltralyticsYOLOEngine:
    def __init__(
        self,
        model_path: str | Path,
        *,
        class_names: Sequence[str] | Mapping[int | str, str],
        confidence_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        image_size: int = 640,
        device: str = "cpu",
    ) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"YOLO model file not found: {path}")
        if path.suffix.casefold() not in {".pt", ".onnx"}:
            raise ValueError("Ultralytics model path must end in .pt or .onnx")
        if device not in {"cpu", "cuda"}:
            raise ValueError("YOLO device must be 'cpu' or 'cuda'")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Ultralytics is an optional dependency and is not installed in this environment"
            ) from exc

        self.model: Any = YOLO(str(path))
        self.expected_names = self._normalize_names(class_names)
        if not self.expected_names:
            raise ValueError("YOLO class_names must not be empty")
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.image_size = image_size
        self.device = "0" if device == "cuda" else "cpu"

    def predict(self, image: np.ndarray) -> InferenceResult:
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Expected a BGR image with shape HxWx3")
        started = time.perf_counter()
        results = self.model.predict(
            source=image,
            conf=self.confidence_threshold,
            iou=self.iou_threshold,
            imgsz=self.image_size,
            device=self.device,
            verbose=False,
        )
        if not results:
            raise ValueError("Ultralytics returned no prediction result")
        result = results[0]
        boxes = getattr(result, "boxes", None)
        names = getattr(result, "names", None)
        actual_names = self._normalize_names(names or {})
        if actual_names != self.expected_names:
            raise ValueError("Configured YOLO class_names do not match model metadata")
        predictions: list[DetectionPrediction] = []
        if boxes is not None and len(boxes):
            xyxy = boxes.xyxy.detach().cpu().numpy()
            confidences = boxes.conf.detach().cpu().numpy()
            class_ids = boxes.cls.detach().cpu().numpy()
            if not (len(xyxy) == len(confidences) == len(class_ids)):
                raise ValueError("Ultralytics returned inconsistent box arrays")
            for box, confidence, class_id_value in zip(xyxy, confidences, class_ids, strict=True):
                class_id = int(class_id_value)
                if not np.isclose(class_id_value, class_id):
                    raise ValueError("Ultralytics class id must be an integer")
                try:
                    label = self.expected_names[class_id]
                except KeyError as exc:
                    raise ValueError(f"No configured class name for YOLO class id {class_id}") from exc
                predictions.append(
                    DetectionPrediction(
                        label=str(label),
                        confidence=float(confidence),
                        bbox=tuple(float(coordinate) for coordinate in box),
                    )
                )

        return InferenceResult(
            detections=predictions,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    @staticmethod
    def _normalize_names(names: Any) -> dict[int, str]:
        if isinstance(names, Mapping):
            try:
                return {int(key): str(value) for key, value in names.items()}
            except (TypeError, ValueError) as exc:
                raise ValueError("YOLO class-name mapping must use integer class IDs") from exc
        if isinstance(names, Sequence) and not isinstance(names, (str, bytes)):
            return {index: str(value) for index, value in enumerate(names)}
        raise ValueError("YOLO model must provide class-name metadata")
