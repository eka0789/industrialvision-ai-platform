"""Drift detection (E2): PSI per feature against training-time reference stats.

Reference statistics (per-feature decile bin edges + bin proportions) are
computed at training time and stored on the registry row
(``reference_stats_json``); the drift service compares live sensor samples
against them. Missing reference → explicit error, never PSI=0.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.db.models import FEATURE_NAMES, ModelVersion, SensorReading

EPS = 1e-6


def compute_reference_stats(samples: list[list[float]]) -> dict[str, Any]:
    """Per-feature decile edges + empirical bin proportions (training time)."""
    matrix = np.asarray(samples, dtype=float)
    stats: dict[str, Any] = {"features": {}, "n_samples": int(len(samples))}
    for index, name in enumerate(FEATURE_NAMES):
        column = matrix[:, index]
        quantiles = np.quantile(column, [i / 10 for i in range(1, 10)])
        edges = [float("-inf"), *[float(q) for q in quantiles], float("inf")]
        counts, _ = np.histogram(column, bins=edges)
        proportions = (counts / max(len(column), 1)).tolist()
        stats["features"][name] = {"edges": edges, "proportions": proportions}
    return stats


def psi(expected: list[float], actual: list[float]) -> float:
    """Population Stability Index between two binned distributions."""
    if len(expected) != len(actual) or not expected:
        raise ValueError("PSI requires equal-length, non-empty distributions")
    total = 0.0
    for e, a in zip(expected, actual):
        e = max(float(e), EPS)
        a = max(float(a), EPS)
        total += (a - e) * math.log(a / e)
    return round(float(total), 6)


def drift_report(db, *, org_id: int, model_id: int, live_samples: list[list[float]] | None = None) -> dict[str, Any]:
    """PSI per feature for one registered model vs its training reference.

    ``live_samples`` defaults to the org's recent sensor readings. Missing
    reference stats raise :class:`ValueError` — an explicit error, never a
    fabricated zero-drift result.
    """
    model = (
        db.query(ModelVersion)
        .filter(ModelVersion.id == model_id, ModelVersion.org_id == org_id)
        .first()
    )
    if model is None:
        raise ValueError(f"model {model_id} not found in organization {org_id}")
    reference = model.reference_stats_json
    if not reference or "features" not in reference:
        raise ValueError(
            f"model {model_id} has no training-time reference stats; drift cannot be computed without fabricating a result"
        )
    if live_samples is None:
        rows = (
            db.query(SensorReading)
            .filter(SensorReading.org_id == org_id)
            .order_by(SensorReading.recorded_at.desc())
            .limit(500)
            .all()
        )
        live_samples = [row.features() for row in rows]
    if not live_samples:
        raise ValueError("no live samples available for drift comparison")

    matrix = np.asarray(live_samples, dtype=float)
    per_feature: dict[str, Any] = {}
    for name in FEATURE_NAMES:
        ref = reference["features"].get(name)
        if not ref:
            per_feature[name] = {"error": "reference missing for feature"}
            continue
        edges = ref["edges"]
        counts, _ = np.histogram(matrix[:, FEATURE_NAMES.index(name)], bins=edges)
        proportions = (counts / len(live_samples)).tolist()
        value = psi(ref["proportions"], proportions)
        per_feature[name] = {
            "psi": value,
            "level": "stable" if value < 0.1 else ("moderate" if value < 0.25 else "significant"),
        }
    overall = max((f.get("psi", 0.0) for f in per_feature.values() if isinstance(f, dict) and "psi" in f), default=0.0)
    return {
        "model_id": model_id,
        "n_reference_samples": reference.get("n_samples"),
        "n_live_samples": len(live_samples),
        "features": per_feature,
        "overall_psi": round(float(overall), 6),
        "drifted": bool(overall >= 0.25),
    }
