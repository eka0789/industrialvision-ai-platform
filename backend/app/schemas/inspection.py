"""Inspection & detection schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DetectionOut(BaseModel):
    label: str
    confidence: float
    bbox: dict | None = None
    area: float | None = None

    class Config:
        from_app = True


class AnomalyOut(BaseModel):
    score: float | None = None
    heatmap_path: str | None = None
    threshold: float | None = None

    class Config:
        from_app = True


class InspectionOut(BaseModel):
    id: int
    camera_id: int
    session_id: int
    product_id: str | None = None
    batch_id: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    detections: list[DetectionOut] = []
    anomaly: AnomalyOut | None = None
    decision: str
    latency_ms: float | None = None
    evidence_path: str | None = None
    error_status: str | None = None
    error_message: str | None = None
    metadata: dict | None = None

    class Config:
        from_app = True


class InspectionBase(BaseModel):
    product_id: str | None = None
    batch_id: str | None = None
    camera_id: int
    model_name: str | None = None
    model_version: str | None = None


class InspectionCreate(InspectionBase):
    pass


class InspectionResultIn(InspectionBase):
    detections: list[dict] | None = None
    anomaly_score: float | None = None
    anomaly_map_path: str | None = None
    decision: str | None = None
    latency_ms: float | None = None
    evidence_filename: str | None = None
    error_status: str | None = None
    error_message: str | None = None
