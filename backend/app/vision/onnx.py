"""Strict ONNX Runtime adapter for an explicit Nx6 detection output contract."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Mapping, Sequence

import cv2
import numpy as np
import onnxruntime as ort

from app.core.config import settings
from app.vision.contracts import DetectionPrediction, InferenceResult


SUPPORTED_OUTPUT_FORMAT = "xyxy_conf_class_nx6"


class ONNXDetectionEngine:
    """Run a model whose output is explicitly configured as rows of xyxy, conf, class."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        output_format: str,
        class_names: Sequence[str] | Mapping[int | str, str],
        device: str = "cpu",
        confidence_threshold: float = 0.25,
        image_size: int = 640,
    ) -> None:
        if output_format != SUPPORTED_OUTPUT_FORMAT:
            raise ValueError(f"Unsupported ONNX output_format: {output_format!r}")
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if device not in {"cpu", "cuda"}:
            raise ValueError("ONNX device must be 'cpu' or 'cuda'")

        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"ONNX model file not found: {self.model_path}")
        self.class_names = class_names
        self.confidence_threshold = confidence_threshold
        self.image_size = image_size

        available = ort.get_available_providers()
        if device == "cuda" and "CUDAExecutionProvider" not in available:
            raise RuntimeError("CUDAExecutionProvider is unavailable; select CPU or install a compatible runtime")
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if device == "cuda" else ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(self.model_path), providers=providers)
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("Detection ONNX model must have exactly one input and one output")

        self.input_name = inputs[0].name
        self.output_name = outputs[0].name
        shape = inputs[0].shape
        if len(shape) != 4 or shape[1] not in (3, "3"):
            raise ValueError("ONNX input must have NCHW layout with three channels")
        self.input_height = shape[2] if isinstance(shape[2], int) and shape[2] > 0 else image_size
        self.input_width = shape[3] if isinstance(shape[3], int) and shape[3] > 0 else image_size

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Expected a BGR image with shape HxWx3")
        if image.shape[0] <= 0 or image.shape[1] <= 0:
            raise ValueError("Image dimensions must be positive")
        resized = cv2.resize(image, (self.input_width, self.input_height), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        chw = np.transpose(rgb, (2, 0, 1))
        return np.ascontiguousarray(chw[np.newaxis, ...], dtype=np.float32) / 255.0

    def postprocess(self, output: np.ndarray, original_shape: tuple[int, int]) -> list[DetectionPrediction]:
        rows = np.asarray(output)
        if rows.ndim == 3 and rows.shape[0] == 1:
            rows = rows[0]
        if rows.ndim != 2 or rows.shape[1] != 6:
            raise ValueError(
                f"Expected ONNX output shape (N, 6) or (1, N, 6); received {tuple(rows.shape)}"
            )
        if not np.isfinite(rows).all():
            raise ValueError("ONNX output contains non-finite values")

        original_height, original_width = original_shape
        scale_x = original_width / self.input_width
        scale_y = original_height / self.input_height
        predictions: list[DetectionPrediction] = []
        for x1, y1, x2, y2, confidence, class_id_value in rows:
            confidence = float(confidence)
            class_id = int(class_id_value)
            if confidence < self.confidence_threshold:
                continue
            if not np.isclose(class_id_value, class_id):
                raise ValueError("ONNX class id must be an integer")
            label = self._class_label(class_id)
            predictions.append(
                DetectionPrediction(
                    label=label,
                    confidence=confidence,
                    bbox=(
                        float(x1) * scale_x,
                        float(y1) * scale_y,
                        float(x2) * scale_x,
                        float(y2) * scale_y,
                    ),
                )
            )
        return predictions

    def _class_label(self, class_id: int) -> str:
        try:
            if isinstance(self.class_names, Mapping):
                return str(self.class_names[class_id])
            return str(self.class_names[class_id])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"No configured class name for model class id {class_id}") from exc

    def predict(self, image: np.ndarray) -> InferenceResult:
        start = time.perf_counter()
        tensor = self.preprocess(image)
        output = self.session.run([self.output_name], {self.input_name: tensor})[0]
        detections = self.postprocess(output, image.shape[:2])
        latency_ms = (time.perf_counter() - start) * 1000.0
        return InferenceResult(detections=detections, latency_ms=latency_ms)


def default_onnx_engine() -> ONNXDetectionEngine:
    return ONNXDetectionEngine(
        Path(settings.MODEL_ROOT) / settings.DEFAULT_ONNX_MODEL,
        output_format=SUPPORTED_OUTPUT_FORMAT,
        class_names=(),
        device=settings.ONNX_DEVICE,
        confidence_threshold=settings.CONF_THRESHOLD,
        image_size=settings.IMAGE_SIZE,
    )
