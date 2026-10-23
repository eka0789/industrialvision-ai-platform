"""ONNX inference engine for IndustrialVision AI defect detection."""

from __future__ import annotations

import os
import time
import numpy as np
import cv2
from typing import Optional, List, Dict, Any, Tuple

from app.core.config import settings


class ONNXInference:
    """Wrapper for ONNX runtime object detection/inference."""

    def __init__(
        self,
        model_path: str | None = None,
        device: str = "cpu",
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
    ):
        self.device = device
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.session = None
        self.input_name = None
        self.output_name = None
        self.input_shape = None

        self._load_model(model_path or settings.DEFAULT_ONNX_MODEL)

    def _load_model(self, model_path: str) -> None:
        """Load ONNX model from path."""
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"ONNX model not found: {model_path}")

        import onnxruntime as ort

        providers = ["CPUExecutionProvider"] if self.device == "cpu" else ["CUDAExecutionProvider"]
        self.session = ort.InferenceSession(model_path, providers=providers)

        # Get input/output details
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        self.input_shape = self.session.get_inputs()[0].shape
        print(f"[ONNX] Model loaded: {model_path} | Input: {self.input_shape} | Device: {self.device}")

    def preprocess(self, frame: np.ndarray) -> np.ndarray:
        """Preprocess frame for ONNX input."""
        # Resize maintaining aspect ratio with padding (letterbox)
        img = cv2.resize(frame, (self.input_shape[3], self.input_shape[2]))
        img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR to RGB, HWC to CHW
        img = np.ascontiguousarray(img, dtype=np.float32) / 255.0
        return img

    def postprocess(self, detections: np.ndarray, orig_shape: Tuple[int, int]) -> List[Dict[str, Any]]:
        """Postprocess ONNX output to detection dicts."""
        # Simplified postprocessing - actual implementation depends on model architecture
        results = []
        if detections.size == 0:
            return results

        # This is model-dependent; placeholder implementation
        for *xyxy, conf, cls in detections:
            if float(conf) < self.conf_threshold:
                continue
            results.append({
                "label": str(cls),
                "confidence": float(conf),
                "bbox": [float(x) for x in xyxy],
            })
        return results

    def predict(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Run inference on a frame and return detections."""
        start = time.time()
        input_tensor = self.preprocess(frame)
        inp = np.expand_dims(input_tensor, 0).astype(np.float32)
        outputs = self.session.run([self.output_name], {self.input_name: inp})[0]
        detections = self.postprocess(outputs, frame.shape[:2])
        latency = (time.time() - start) * 1000  # ms
        return {"detections": detections, "latency_ms": latency}


# Global inference instance (lazy)
_inference: Optional[ONNXInference] = None


def get_inference() -> ONNXInference:
    """Get or create global ONNX inference instance."""
    global _inference
    if _inference is None:
        _inference = ONNXInference(
            model_path=settings.DEFAULT_ONNX_MODEL,
            device=settings.ONNX_DEVICE,
            conf_threshold=settings.CONF_THRESHOLD,
            iou_threshold=settings.IOU_THRESHOLD,
        )
    return _inference


def predict_frame(frame: np.ndarray) -> Dict[str, Any]:
    """Convenience function to predict on a frame."""
    return get_inference().predict(frame)