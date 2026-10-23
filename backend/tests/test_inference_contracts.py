"""Model-neutral contracts, strict ONNX outputs, and model path safety."""

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from pydantic import ValidationError

from app.db.models import ModelVersion
from app.vision.contracts import AnomalyPrediction, DetectionPrediction, InferenceResult
from app.vision.factory import create_detection_engine, resolve_model_path
from app.vision.onnx import ONNXDetectionEngine
from app.vision.yolo import UltralyticsYOLOEngine


class _FakeTensor:
    def __init__(self, value):
        self.value = np.asarray(value)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.value


class _FakeBoxes:
    def __init__(self):
        self.xyxy = _FakeTensor([[1, 2, 9, 10]])
        self.conf = _FakeTensor([0.95])
        self.cls = _FakeTensor([0])

    def __len__(self):
        return 1


class _FakeYOLOModel:
    def __init__(self, names):
        self.names = names

    def predict(self, **_kwargs):
        return [types.SimpleNamespace(boxes=_FakeBoxes(), names=self.names)]


class InferenceContractTests(unittest.TestCase):
    def test_detection_schema_validates_box_confidence_and_finite_values(self):
        prediction = DetectionPrediction(label="scratch", confidence=0.8, bbox=(1, 2, 9, 10))
        self.assertEqual(prediction.label, "scratch")
        with self.assertRaises(ValidationError):
            DetectionPrediction(label="scratch", confidence=1.1, bbox=(1, 2, 9, 10))
        with self.assertRaises(ValidationError):
            DetectionPrediction(label="scratch", confidence=0.8, bbox=(5, 2, 4, 10))
        with self.assertRaises(ValidationError):
            DetectionPrediction(label="scratch", confidence=0.8, bbox=(1, 2, float("nan"), 10))

    def test_anomaly_score_is_not_assumed_to_be_probability(self):
        output = AnomalyPrediction(score=4.25)
        result = InferenceResult(detections=[], anomaly=output, latency_ms=1.0)
        self.assertEqual(result.anomaly.score, 4.25)

    def test_onnx_detection_layout_accepts_nx6_and_scales_boxes(self):
        engine = object.__new__(ONNXDetectionEngine)
        engine.input_width = 100
        engine.input_height = 50
        engine.confidence_threshold = 0.25
        engine.class_names = ["good", "scratch"]
        raw = np.array([[2, 3, 20, 30, 0.9, 1]], dtype=np.float32)
        detections = engine.postprocess(raw, (100, 200))
        self.assertEqual(detections[0].label, "scratch")
        self.assertEqual(detections[0].bbox, (4.0, 6.0, 40.0, 60.0))

    def test_onnx_rejects_unsupported_shapes_and_unknown_classes(self):
        engine = object.__new__(ONNXDetectionEngine)
        engine.input_width = 640
        engine.input_height = 640
        engine.confidence_threshold = 0.25
        engine.class_names = ["good"]
        with self.assertRaisesRegex(ValueError, "Expected ONNX output shape"):
            engine.postprocess(np.zeros((1, 84, 8400), dtype=np.float32), (640, 640))
        with self.assertRaisesRegex(ValueError, "No configured class name"):
            engine.postprocess(np.array([[1, 1, 5, 5, 0.9, 2]], dtype=np.float32), (640, 640))
        with self.assertRaisesRegex(ValueError, "non-finite"):
            engine.postprocess(np.array([[1, 1, 5, 5, np.nan, 0]], dtype=np.float32), (640, 640))

    def test_model_loading_failure_is_explicit(self):
        with self.assertRaises(FileNotFoundError):
            ONNXDetectionEngine(
                "missing-model.onnx",
                output_format="xyxy_conf_class_nx6",
                class_names=["scratch"],
            )

    def test_onnx_requires_explicit_output_format_and_class_names(self):
        with self.assertRaisesRegex(ValueError, "output_format"):
            create_detection_engine(
                ModelVersion(
                    name="unsupported-format",
                    framework="onnx",
                    source_path="missing.onnx",
                    config_json={"output_format": "unknown", "class_names": ["scratch"]},
                )
            )
        with self.assertRaisesRegex(ValueError, "class_names"):
            create_detection_engine(
                ModelVersion(
                    name="no-classes",
                    framework="onnx",
                    source_path="missing.onnx",
                    config_json={"output_format": "xyxy_conf_class_nx6"},
                )
            )

    def test_model_paths_cannot_escape_configured_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "models"
            root.mkdir()
            model = root / "valid.onnx"
            model.write_bytes(b"placeholder")
            outside = Path(temp_dir) / "outside.onnx"
            outside.write_bytes(b"placeholder")
            self.assertEqual(resolve_model_path(root, "valid.onnx"), model.resolve())
            with self.assertRaisesRegex(ValueError, "inside MODEL_ROOT"):
                resolve_model_path(root, "../outside.onnx")

    def test_yolo_rejects_unsupported_artifact_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "weights.bin"
            model_path.write_bytes(b"not a model")
            with self.assertRaisesRegex(ValueError, "must end in .pt or .onnx"):
                UltralyticsYOLOEngine(model_path, class_names=["scratch"])

    def test_yolo_adapter_maps_actual_model_classes_to_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "weights.pt"
            model_path.write_bytes(b"stub weights")
            fake_module = types.ModuleType("ultralytics")
            fake_module.YOLO = lambda _path: _FakeYOLOModel({0: "scratch"})
            with patch.dict("sys.modules", {"ultralytics": fake_module}):
                engine = UltralyticsYOLOEngine(model_path, class_names=["scratch"])
                result = engine.predict(np.zeros((20, 30, 3), dtype=np.uint8))
            self.assertEqual(result.detections[0].label, "scratch")
            self.assertEqual(result.detections[0].confidence, 0.95)

    def test_yolo_adapter_rejects_class_metadata_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "weights.pt"
            model_path.write_bytes(b"stub weights")
            fake_module = types.ModuleType("ultralytics")
            fake_module.YOLO = lambda _path: _FakeYOLOModel({0: "scratch"})
            with patch.dict("sys.modules", {"ultralytics": fake_module}):
                engine = UltralyticsYOLOEngine(model_path, class_names=["dent"])
                with self.assertRaisesRegex(ValueError, "do not match"):
                    engine.predict(np.zeros((20, 30, 3), dtype=np.uint8))

    def test_yolo_factory_requires_explicit_class_names(self):
        with self.assertRaisesRegex(ValueError, "class_names"):
            create_detection_engine(
                ModelVersion(name="yolo", framework="ultralytics", source_path="missing.pt")
            )


if __name__ == "__main__":
    unittest.main()
