"""Regression coverage for fail-safe PASS/FAIL/REVIEW/ERROR decisions."""

import unittest

from app.services.decision import DecisionRules, decide_inspection


class DecisionTests(unittest.TestCase):
    def test_valid_pass_requires_successful_inference(self):
        result = decide_inspection([], rules=DecisionRules(reject_labels=frozenset({"scratch"})))
        self.assertEqual(result["decision"], "PASS")
        self.assertIn("passed", result["reason"])

    def test_configured_defect_is_fail(self):
        result = decide_inspection(
            [{"label": "scratch", "confidence": 0.91}],
            rules=DecisionRules(reject_labels=frozenset({"scratch"})),
        )
        self.assertEqual(result["decision"], "FAIL")

    def test_near_threshold_anomaly_requires_review(self):
        result = decide_inspection(
            [], 0.48,
            rules=DecisionRules(anomaly_threshold=0.5, review_band=0.05),
        )
        self.assertEqual(result["decision"], "REVIEW")

    def test_inference_failure_never_passes(self):
        result = decide_inspection(None, inference_error="model unavailable")
        self.assertEqual(result["decision"], "ERROR")
        self.assertEqual(result["error"], "model unavailable")

    def test_missing_configured_anomaly_result_requires_review(self):
        result = decide_inspection([], rules=DecisionRules(anomaly_threshold=0.5))
        self.assertEqual(result["decision"], "REVIEW")

    def test_unconfigured_anomaly_score_is_not_misrepresented_as_probability(self):
        result = decide_inspection([], 0.99)
        self.assertEqual(result["decision"], "PASS")
        self.assertIn("configured", result["reason"])

    def test_reject_detection_below_confidence_does_not_fail(self):
        result = decide_inspection(
            [{"label": "scratch", "confidence": 0.2}],
            rules=DecisionRules(reject_labels=frozenset({"scratch"}), detection_confidence=0.5),
        )
        self.assertEqual(result["decision"], "PASS")

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            DecisionRules(anomaly_threshold=1.1)


if __name__ == "__main__":
    unittest.main()
