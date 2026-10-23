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
