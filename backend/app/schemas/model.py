"""Model registry & evaluation schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelVersionOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    framework: str
    framework_version: str | None = None
    source_path: str | None = None
    device: str
    config_json: dict | None = None
    is_active: bool
    status: str

    class Config:
        from_app = True


class ModelVersionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    framework: str = Field(min_length=1, max_length=50)
    framework_version: str | None = None
    source_path: str | None = None
    device: str = "cpu"
    config_json: dict | None = None
    is_active: bool = False


class ModelVersionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    framework: str | None = Field(default=None, min_length=1, max_length=50)
    framework_version: str | None = None
    source_path: str | None = None
    device: str | None = None
    config_json: dict | None = None
    is_active: bool | None = None


class EvaluationIn(BaseModel):
    task_type: str = Field(min_length=1, max_length=50)
    metrics_json: dict | None = None
    hardware: str | None = None
    input_size: str | None = None
    batch_size: int | None = None


class EvaluationOut(BaseModel):
    id: int
    model_version_id: int
    task_type: str
    metrics_json: dict | None = None
    hardware: str | None = None
    input_size: str | None = None
    batch_size: int | None = None
    measured_at: str

    class Config:
        from_app = True
