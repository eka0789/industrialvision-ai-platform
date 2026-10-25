"""Camera API schemas."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.core.constants import CameraSourceType


def _reject_embedded_rtsp_credentials(value: str | None) -> str | None:
    if value:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() in {"rtsp", "rtsps"} and (parsed.username or parsed.password):
            raise ValueError("RTSP credentials must not be embedded in camera URLs")
    return value


class CameraListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    camera_identity: str | None = None
    source_type: str
    status: str
    line_id: int | None = None
    resolution: str | None = None
    fps_input: float | None = None
    last_inspection_at: datetime | None = None


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    camera_identity: str | None = Field(default=None, max_length=255)
    source_type: str = Field(default=CameraSourceType.SIMULATED, max_length=20)
    source_url: str | None = Field(default=None, max_length=2048)
    line_id: int | None = None
    resolution: str | None = Field(default=None, max_length=40)
    fps_input: float | None = Field(default=None, gt=0, le=240)
    is_active: bool = True

    @field_validator("source_url")
    @classmethod
    def reject_embedded_credentials(cls, value: str | None) -> str | None:
        return _reject_embedded_rtsp_credentials(value)


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_type: str | None = Field(default=None, max_length=20)
    source_url: str | None = Field(default=None, max_length=2048)
    line_id: int | None = None
    resolution: str | None = Field(default=None, max_length=40)
    fps_input: float | None = Field(default=None, gt=0, le=240)
    is_active: bool | None = None

    @field_validator("source_url")
    @classmethod
    def reject_embedded_credentials(cls, value: str | None) -> str | None:
        return _reject_embedded_rtsp_credentials(value)


class CameraOut(CameraListOut):
    source_url: str | None = None
    last_error: str | None = None
    is_active: bool

    @field_serializer("source_url")
    def redact_source_url(self, value: str | None) -> str | None:
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            return parsed._replace(netloc=f"[REDACTED]@{host}").geturl()
        return value


# Session schemas
class SessionBase(BaseModel):
    camera_id: int = Field(gt=0)
    model_version_id: int = Field(gt=0)


class SessionCreate(SessionBase):
    pass


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    camera_id: int
    model_version_id: int
    status: str
    start_time: datetime
    end_time: datetime | None = None


class SessionStatusOut(BaseModel):
    session_id: int
    camera_id: int
    model_version_id: int
    status: str
    start_time: str | None = None
    end_time: str | None = None
    metrics: dict | None = None


class VideoInspectionRequest(BaseModel):
    camera_id: int = Field(gt=0)
    file_path: str = Field(min_length=1)
    frame_interval: int = Field(default=1, ge=1, le=300)
    max_frames: int | None = Field(default=None, ge=1)


class VideoInspectionResultOut(BaseModel):
    camera_id: int
    total_frames: int
    processed_frames: int
    dropped_frames: int
    fps: float
    avg_latency_ms: float
    duration_sec: float
    detections: list[dict]
    errors: list[str]
    # Runtime metrics snapshot (T22): latency windows, FPS, resource usage,
    # counters, unit registry, and hardware metadata.
    metrics: dict | None = None
