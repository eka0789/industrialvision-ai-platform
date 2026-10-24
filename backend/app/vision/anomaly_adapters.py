"""Optional PatchCore/PaDiM-style anomaly adapters with compatibility gates.

No Torch/Anomalib import at module load. Numpy fallback keeps adapters testable;
production Torch backends require explicit compatible versions.
"""

from __future__ import annotations

import importlib.util
import platform
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.vision.anomaly import AnomalyDetector
from app.vision.contracts import AnomalyPrediction


@dataclass(frozen=True)
class CompatibilityReport:
    supported: bool
    python_version: str
    torch_available: bool
    torch_version: str | None
    reason: str | None = None


def check_anomaly_compatibility(
    *,
    require_torch: bool = False,
    min_python: tuple[int, int] = (3, 10),
) -> CompatibilityReport:
    """Check runtime before loading optional PatchCore/PaDiM dependencies."""
    py = platform.python_version()
    if tuple(map(int, py.split(".")[:2])) < min_python:
        return CompatibilityReport(False, py, False, None, f"Python >= {min_python[0]}.{min_python[1]} required")

    torch_available = importlib.util.find_spec("torch") is not None
    torch_version: str | None = None
    if torch_available:
        try:
            import torch  # type: ignore

            torch_version = str(torch.__version__)
        except Exception as exc:
            return CompatibilityReport(False, py, False, None, f"Torch import failed: {exc}")

    if require_torch and not torch_available:
        return CompatibilityReport(False, py, False, None, "Torch is not installed; optional anomaly adapter unavailable")
    return CompatibilityReport(True, py, torch_available, torch_version)


def _feature(image: np.ndarray, size: int = 32) -> np.ndarray:
    """Small deterministic embedding for compatibility-safe baseline."""
    if image.ndim == 3:
        image = image.mean(axis=2)
    image = np.asarray(image, dtype=np.float32)
    h, w = image.shape[:2]
    ys = np.linspace(0, max(0, h - 1), size).astype(int)
    xs = np.linspace(0, max(0, w - 1), size).astype(int)
    return image[np.ix_(ys, xs)].reshape(-1) / 255.0


class PatchCoreAdapter(AnomalyDetector):
    """Nearest-neighbor memory-bank anomaly detector."""

    method = "patchcore"

    def __init__(self, threshold: float | None = None, require_torch: bool = False):
        super().__init__(threshold)
        report = check_anomaly_compatibility(require_torch=require_torch)
        if not report.supported:
            raise RuntimeError(f"PatchCore compatibility check failed: {report.reason}")
        self.compatibility = report
        self.memory_bank: np.ndarray | None = None

    def fit(self, normal_images: list[np.ndarray]) -> None:
        if not normal_images:
            raise ValueError("PatchCore requires at least one normal sample")
        self.memory_bank = np.stack([_feature(image) for image in normal_images])
        self.is_trained = True

    def predict(self, image: np.ndarray) -> AnomalyPrediction:
        if not self.is_trained or self.memory_bank is None:
            raise RuntimeError("PatchCore detector not trained on normal samples")
        query = _feature(image)
        distances = np.linalg.norm(self.memory_bank - query, axis=1)
        score = float(distances.min())
        heatmap = [[score]]
        return AnomalyPrediction(score=score, heatmap=heatmap)

    def get_metadata(self) -> dict[str, Any]:
        return {
            **super().get_metadata(),
            "method": self.method,
            "memory_bank_size": int(self.memory_bank.shape[0]) if self.memory_bank is not None else 0,
            "compatibility": self.compatibility.__dict__,
        }


class PaDiMAdapter(AnomalyDetector):
    """Mean/covariance embedding anomaly detector."""

    method = "padim"

    def __init__(self, threshold: float | None = None, require_torch: bool = False):
        super().__init__(threshold)
        report = check_anomaly_compatibility(require_torch=require_torch)
        if not report.supported:
            raise RuntimeError(f"PaDiM compatibility check failed: {report.reason}")
        self.compatibility = report
        self.mean: np.ndarray | None = None
        self.variance: np.ndarray | None = None

    def fit(self, normal_images: list[np.ndarray]) -> None:
        if not normal_images:
            raise ValueError("PaDiM requires at least one normal sample")
        features = np.stack([_feature(image) for image in normal_images])
        self.mean = features.mean(axis=0)
        self.variance = features.var(axis=0) + 1e-6
        self.is_trained = True

    def predict(self, image: np.ndarray) -> AnomalyPrediction:
        if not self.is_trained or self.mean is None or self.variance is None:
            raise RuntimeError("PaDiM detector not trained on normal samples")
        query = _feature(image)
        score = float(np.mean((query - self.mean) ** 2 / self.variance))
        return AnomalyPrediction(score=score, heatmap=[[score]])

    def get_metadata(self) -> dict[str, Any]:
        return {
            **super().get_metadata(),
            "method": self.method,
            "feature_dimensions": int(self.mean.size) if self.mean is not None else 0,
            "compatibility": self.compatibility.__dict__,
        }
