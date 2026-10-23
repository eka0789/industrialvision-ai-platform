"""Common response/error schemas."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class TimeBase(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None


class Message(BaseModel):
    detail: str


class ErrorDetail(BaseModel):
    error: str
    message: str
    code: int = 400


class PaginatedResponse(BaseModel, extra="allow"):
    items: list[dict]
    total: int
    page: int
    page_size: int
    has_more: bool


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str
    database: str
    camera_connections: int


class SuccessResponse(BaseModel, extra="allow"):
    detail: str
