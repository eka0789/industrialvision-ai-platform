"""Anomaly detection adapter: memory-bank/distribution-based unsupervised detection.

Distinct from object detection. Scores are model-dependent and not calibrated probabilities.
Requires training on normal samples before inference.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from app.vision.contracts import AnomalyPrediction


class AnomalyDetector:
    """Base anomaly detector interface."""

    def __init__(self, threshold: float | None = None):
        self.threshold = threshold
        self.is_trained = False

    def fit(self, normal_images: list[np.ndarray]) -> None:
        """Train on normal samples (no anomalies)."""
        raise NotImplementedError

    def predict(self, image: np.ndarray) -> AnomalyPrediction:
        """Return anomaly score and optional heatmap. Score is NOT a probability."""
        raise NotImplementedError

    def get_metadata(self) -> dict[str, Any]:
        """Return detector metadata: threshold, calibrated status, training info."""
        return {
            "threshold": self.threshold,
            "is_calibrated_probability": False,
            "is_trained": self.is_trained,
        }


class StubAnomalyDetector(AnomalyDetector):
    """Stub detector for testing: returns fixed score."""

    def __init__(self, fixed_score: float = 0.0, threshold: float | None = None):
        super().__init__(threshold)
        self.fixed_score = fixed_score
        self.is_trained = True

    def fit(self, normal_images: list[np.ndarray]) -> None:
        self.is_trained = True

    def predict(self, image: np.ndarray) -> AnomalyPrediction:
        if not self.is_trained:
            raise RuntimeError("Detector not trained on normal samples")
        return AnomalyPrediction(score=self.fixed_score, heatmap=None)


def validate_anomaly_score(score: float) -> None:
    """Validate anomaly score is finite (not NaN/Inf)."""
    if not math.isfinite(score):
        raise ValueError(f"Anomaly score {score} is not finite")


def create_anomaly_detector(
    method: str,
    model_path: Path | None = None,
    threshold: float | None = None,
) -> AnomalyDetector:
    """Factory for anomaly detectors. Stub only; PatchCore/PaDiM gated by T16."""
    if method == "stub":
        return StubAnomalyDetector(threshold=threshold)
    # ponytail: PatchCore/PaDiM adapters added in T16 after compatibility check
    raise ValueError(f"Unsupported anomaly method: {method}")
