"""Tests for Phase 3: Anomaly interfaces, adapters, and evaluation metrics."""

from __future__ import annotations

import unittest
import numpy as np

from app.vision.anomaly import (
    AnomalyDetector,
    StubAnomalyDetector,
    validate_anomaly_score,
    create_anomaly_detector,
)
from app.vision.anomaly_adapters import (
    PatchCoreAdapter,
    PaDiMAdapter,
    check_anomaly_compatibility,
)
from app.vision.evaluation import (
    compute_image_auroc,
    compute_pixel_auroc,
    compute_optimal_f1,
    compute_pro,
    evaluate_anomaly,
    generate_synthetic_anomaly_data,
)


class AnomalyInterfaceTests(unittest.TestCase):
    def test_stub_detector_predicts_score_without_probability_label(self):
        detector = StubAnomalyDetector(fixed_score=0.75, threshold=0.5)
        meta = detector.get_metadata()
        self.assertFalse(meta["is_calibrated_probability"])
        self.assertEqual(meta["threshold"], 0.5)

        img = np.zeros((10, 10, 3), dtype=np.uint8)
        pred = detector.predict(img)
        self.assertEqual(pred.score, 0.75)
        self.assertIsNone(pred.heatmap)

    def test_validate_anomaly_score_rejects_inf_nan(self):
        validate_anomaly_score(0.5)
        with self.assertRaises(ValueError):
            validate_anomaly_score(float("inf"))
        with self.assertRaises(ValueError):
            validate_anomaly_score(float("nan"))


class AnomalyAdapterTests(unittest.TestCase):
    def test_compatibility_report_is_available(self):
        report = check_anomaly_compatibility()
        self.assertTrue(report.supported)
        self.assertIsInstance(report.python_version, str)

    def test_patchcore_adapter_fits_and_predicts(self):
        adapter = PatchCoreAdapter(threshold=0.5)
        normal = [np.ones((20, 20, 3), dtype=np.uint8) * 10 for _ in range(3)]
        adapter.fit(normal)

        # Similar image -> low score
        pred_low = adapter.predict(np.ones((20, 20, 3), dtype=np.uint8) * 10)
        # Very different image -> higher score
        pred_high = adapter.predict(np.ones((20, 20, 3), dtype=np.uint8) * 200)

        self.assertGreater(pred_high.score, pred_low.score)
        self.assertIsNotNone(pred_high.heatmap)

    def test_padim_adapter_fits_and_predicts(self):
        adapter = PaDiMAdapter(threshold=1.0)
        normal = [np.ones((20, 20, 3), dtype=np.uint8) * 50 for _ in range(5)]
        adapter.fit(normal)

        pred_normal = adapter.predict(np.ones((20, 20, 3), dtype=np.uint8) * 50)
        pred_outlier = adapter.predict(np.ones((20, 20, 3), dtype=np.uint8) * 250)

        self.assertGreater(pred_outlier.score, pred_normal.score)


class AnomalyEvaluationTests(unittest.TestCase):
    def test_auroc_on_synthetic_data_is_high(self):
        y_ti, y_si, y_tp, y_sp = generate_synthetic_anomaly_data(n_normal=20, n_anomaly=20)
        auroc = compute_image_auroc(y_ti, y_si)
        self.assertGreater(auroc, 0.8)

    def test_optimal_f1_finds_valid_threshold(self):
        y_ti, y_si, _, _ = generate_synthetic_anomaly_data(n_normal=10, n_anomaly=10)
        f1, th = compute_optimal_f1(y_ti, y_si)
        self.assertGreaterEqual(f1, 0.5)
        self.assertLessEqual(f1, 1.0)

    def test_full_evaluation_pipeline(self):
        y_ti, y_si, y_tp, y_sp = generate_synthetic_anomaly_data(n_normal=10, n_anomaly=10)
        result = evaluate_anomaly(y_ti, y_si, y_tp, y_sp)
        self.assertIsNotNone(result.image_level)
        self.assertIsNotNone(result.pixel_level)
        self.assertGreater(result.image_level.auroc, 0.5)
        self.assertGreater(result.pixel_level.auroc, 0.5)


if __name__ == "__main__":
    unittest.main()
