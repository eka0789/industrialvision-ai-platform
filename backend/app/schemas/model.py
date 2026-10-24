"""Model registry & evaluation schemas (T20).

Registry inputs are constrained here: framework/task allowlists, normalized
thresholds, class lists, license and training-data provenance. Artifact
location is validated server-side against MODEL_ROOT in the router — API
input never names an arbitrary filesystem path.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator

SUPPORTED_FRAMEWORKS = ("onnx", "onnxruntime", "yolo", "ultralytics")
SUPPORTED_TASKS = ("object_detection", "anomaly_image", "anomaly_segmentation")
ALLOWED_ARTIFACT_EXTENSIONS = (".onnx", ".pt", ".pth")

Framework = Literal["onnx", "onnxruntime", "yolo", "ultralytics"]
TaskType = Literal["object_detection", "anomaly_image", "anomaly_segmentation"]


class ModelVersionOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    framework: str
    framework_version: str | None = None
    task_type: str | None = None
    artifact_id: str | None = None
    version: str | None = None
    license: str | None = None
    checksum: str | None = None
    classes: list[str] | None = None
    thresholds: dict[str, float] | None = None
    provenance: dict | None = None
    source_path: str | None = None
    device: str
    config_json: dict | None = None
    is_active: bool
    status: str

    class Config:
        from_app = True


class _ThresholdValidation(BaseModel):
    # check_fields=False: fields live on the concrete Create/Update subclasses.
    @field_validator("thresholds", mode="after", check_fields=False)
    @classmethod
    def _thresholds_in_range(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        for key, threshold in value.items():
            if not key.strip():
                raise ValueError("Threshold names must not be empty")
            if not 0.0 <= threshold <= 1.0:
                raise ValueError(f"Threshold {key!r} must lie in [0, 1]")
        return value

    @field_validator("classes", mode="after", check_fields=False)
    @classmethod
    def _classes_non_empty(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [name.strip() for name in value]
        if any(not name for name in cleaned):
            raise ValueError("Class names must not be empty")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Class names must be unique")
        return cleaned


class ModelVersionCreate(_ThresholdValidation):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    framework: Framework
    framework_version: str | None = Field(default=None, max_length=50)
    task_type: TaskType
    # Relative artifact reference resolved strictly under MODEL_ROOT by the server.
    source_path: str = Field(min_length=1, max_length=500)
    artifact_id: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=50)
    license: str = Field(min_length=1, max_length=200)
    classes: list[str] = Field(default_factory=list, max_length=4096)
    thresholds: dict[str, float] | None = None
    provenance: dict | None = None
    device: str = Field(default="cpu", max_length=10)
    config_json: dict | None = None
    is_active: bool = False

    @field_validator("provenance", mode="after")
    @classmethod
    def _provenance_object(cls, value: dict | None) -> dict | None:
        if value is not None and not value:
            raise ValueError("Provenance, when provided, must not be empty")
        return value


class ModelVersionUpdate(_ThresholdValidation):
    description: str | None = Field(default=None, max_length=1000)
    framework: Framework | None = None
    framework_version: str | None = Field(default=None, max_length=50)
    task_type: TaskType | None = None
    source_path: str | None = Field(default=None, max_length=500)
    artifact_id: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=50)
    license: str | None = Field(default=None, min_length=1, max_length=200)
    classes: list[str] | None = Field(default=None, max_length=4096)
    thresholds: dict[str, float] | None = None
    provenance: dict | None = None
    config_json: dict | None = None
    is_active: bool | None = None

    @field_validator("provenance", mode="after")
    @classmethod
    def _provenance_object(cls, value: dict | None) -> dict | None:
        if value is not None and not value:
            raise ValueError("Provenance, when provided, must not be empty")
        return value


class EvaluationIn(BaseModel):
    """Metrics accepted only for a completed run on an identified split (T21 gate)."""

    task_type: TaskType
    run_id: str = Field(min_length=1, max_length=120)
    split: str = Field(min_length=1, max_length=120)
    status: Literal["completed"] = "completed"
    metrics_json: dict = Field(min_length=1)
    hardware: str | None = Field(default=None, max_length=200)
    input_size: str | None = Field(default=None, max_length=50)
    batch_size: int | None = Field(default=None, ge=1)


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
