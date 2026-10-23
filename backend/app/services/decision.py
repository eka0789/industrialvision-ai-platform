"""Configurable, fail-safe inspection decision rules.

Anomaly scores are consumed only when supplied by an anomaly model. Detection
confidence is not a proxy for anomaly probability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Decision = Literal["PASS", "FAIL", "REVIEW", "ERROR"]


@dataclass(frozen=True)
class DecisionRules:
    """Business rules applied to outputs from separately validated models."""

    reject_labels: frozenset[str] = field(default_factory=frozenset)
    detection_confidence: float = 0.5
    anomaly_threshold: float | None = None
    review_band: float = 0.05

    def __post_init__(self) -> None:
        if not 0.0 <= self.detection_confidence <= 1.0:
            raise ValueError("detection_confidence must be between 0 and 1")
        if self.anomaly_threshold is not None and not 0.0 <= self.anomaly_threshold <= 1.0:
            raise ValueError("anomaly_threshold must be between 0 and 1")
        if not 0.0 <= self.review_band <= 0.5:
            raise ValueError("review_band must be between 0 and 0.5")


def decide_inspection(
    detections: list[dict[str, Any]] | None,
    anomaly_score: float | None = None,
    *,
    inference_error: str | None = None,
    rules: DecisionRules | None = None,
) -> dict[str, Any]:
    """Return a decision and operator-readable rationale.

    Missing detections or any inference exception yield ERROR; incomplete model
    evidence never becomes an implicit PASS. Unconfigured anomaly outputs are
    retained as evidence but cannot change the decision.
    """
    active_rules = rules or DecisionRules()
    if inference_error:
        return {"decision": "ERROR", "reason": "Inference failed", "error": inference_error}
    if detections is None:
        return {"decision": "ERROR", "reason": "Detection result is missing", "error": None}
    if anomaly_score is not None and not 0.0 <= anomaly_score <= 1.0:
        return {"decision": "ERROR", "reason": "Anomaly score is outside the expected 0-1 range", "error": None}

    failed = [
        item for item in detections
        if str(item.get("label", "")).casefold() in {label.casefold() for label in active_rules.reject_labels}
        and float(item.get("confidence", 0.0)) >= active_rules.detection_confidence
    ]
    if failed:
        return {
            "decision": "FAIL",
            "reason": f"{len(failed)} configured reject detection(s) met the confidence threshold",
            "error": None,
        }

    threshold = active_rules.anomaly_threshold
    if threshold is not None:
        if anomaly_score is None:
            return {"decision": "REVIEW", "reason": "Configured anomaly model returned no score", "error": None}
        if anomaly_score >= threshold:
            return {"decision": "FAIL", "reason": "Anomaly score met or exceeded the configured threshold", "error": None}
        if anomaly_score >= threshold - active_rules.review_band:
            return {"decision": "REVIEW", "reason": "Anomaly score is within the configured review band", "error": None}

    return {"decision": "PASS", "reason": "All configured inspection rules passed", "error": None}
