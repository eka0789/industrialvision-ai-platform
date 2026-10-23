"""Camera schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.constants import CameraSourceType, CameraStatus


class CameraListOut(BaseModel):
    id: int
    name: str
    camera_identity: str | None = None
    source_type: str
    status: str
    line_id: int | None = None
    resolution: str | None = None
    fps_input: str | None = None
    last_inspection_at: str | None = None

    class Config:
        from_app = True
        use_enum_values = True


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    camera_identity: str | None = Field(default=None, max_length=255)
    source_type: str = Field(default=CameraSourceType.SIMULATED, max_length=20)
    source_url: str | None = Field(default=None, max_length=2048)
    line_id: int | None = None
    resolution: str | None = Field(default=None, max_length=40)
    fps_input: str | None = Field(default=None, max_length=20)
    is_active: bool = True


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_type: str | None = Field(default=None, max_length=20)
    source_url: str | None = Field(default=None, max_length=2048)
    line_id: int | None = None
    resolution: str | None = Field(default=None, max_length=40)
    fps_input: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None


class CameraOut(CameraListOut):
    id: int
    camera_identity: str | None
    source_type: str
    source_url: str | None = None
    line_id: int | None = None
    resolution: str | None = None
    fps_input: str | None = None
    status: str
    last_inspection_at: str | None = None
    last_error: str | None = None
    is_active: bool

    class Config:
        from_app = True
        use_enum_values = True
