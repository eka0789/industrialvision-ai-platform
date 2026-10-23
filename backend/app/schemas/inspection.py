"""Validated inspection request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

InspectionDecision = Literal["PASS", "FAIL", "REVIEW", "ERROR"]


class DetectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: dict[str, float] | None = None
    area: float | None = None


class AnomalyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float | None = None
    heatmap_path: str | None = None
    threshold: float | None = None


class InspectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    timestamp: datetime
    camera_id: int
    session_id: int
    product_id: str | None = None
    batch_id: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    detections: list[DetectionOut] = Field(default_factory=list)
    anomaly: AnomalyOut | None = None
    decision: InspectionDecision
    decision_reason: str | None = None
    latency_ms: float | None = None
    evidence_path: str | None = None
    error_status: str | None = None
    error_message: str | None = None
    metadata: dict | None = None


class InspectionPage(BaseModel):
    items: list[InspectionOut]
    total: int
    page: int
    page_size: int
    has_more: bool


class InspectionBase(BaseModel):
    product_id: str | None = Field(default=None, max_length=200)
    batch_id: str | None = Field(default=None, max_length=200)
    camera_id: int = Field(gt=0)
    model_name: str | None = Field(default=None, max_length=200)
    model_version: str | None = Field(default=None, max_length=50)


class InspectionCreate(InspectionBase):
    pass


class InspectionResultIn(InspectionBase):
    detections: list[dict] | None = None
    anomaly_score: float | None = None
    anomaly_map_path: str | None = None
    decision: InspectionDecision | None = None
    latency_ms: float | None = None
    evidence_filename: str | None = None
    error_status: str | None = None
    error_message: str | None = None
