"""Model-neutral computer-vision result contracts."""

from __future__ import annotations

import math
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DetectionPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: tuple[float, float, float, float]

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: tuple[float, float, float, float]):
        if not all(math.isfinite(coordinate) for coordinate in value):
            raise ValueError("Bounding-box coordinates must be finite")
        if value[2] <= value[0] or value[3] <= value[1]:
            raise ValueError("Bounding-box maximum coordinates must exceed minimum coordinates")
        return value


class AnomalyPrediction(BaseModel):
    """Raw model score and optional heatmap location; score is not a probability."""

    model_config = ConfigDict(extra="forbid")

    score: float
    heatmap: list[list[float]] | None = None

    @field_validator("score")
    @classmethod
    def validate_finite_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Anomaly score must be finite")
        return value


class InferenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detections: list[DetectionPrediction] = Field(default_factory=list)
    anomaly: AnomalyPrediction | None = None
    latency_ms: float = Field(ge=0.0)
    model_name: str | None = None
    model_version: str | None = None

    @field_validator("latency_ms")
    @classmethod
    def validate_finite_latency(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Inference latency must be finite")
        return value


class DetectionEngine(Protocol):
    def predict(self, image: Any) -> InferenceResult: ...


class AnomalyEngine(Protocol):
    def predict(self, image: Any) -> AnomalyPrediction: ...
