"""Database ORM models for IndustrialVision AI.

Schema surfaces (core):
- organizations (multi-tenancy root; every tenant table carries org_id)
- roles, users
- production_lines, cameras
- model_versions, model_evaluations
- inspection_sessions, inspections, detection_results, anomaly_results
- system_events, audit_logs
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Float, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

BC = DateTime(timezone=True)


def now() -> datetime:
    return datetime.now(timezone.utc)


class Organization(Base):
    """Tenant root. Every tenant-scoped table carries ``org_id`` (A6).

    Row id 1 is the bootstrap organization created by migration
    ``a00000000005``; existing single-tenant installs land there.
    """

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    users: Mapped[list["User"]] = relationship(back_populates="organization")

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, name={self.name})>"


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    users: Mapped[list["User"]] = relationship(back_populates="role")

    def __repr__(self) -> str:
        return f"<Role(id={self.id}, name={self.name})>"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    role: Mapped["Role"] = relationship(back_populates="users")
    organization: Mapped["Organization"] = relationship(back_populates="users")
    audits: Mapped[list["AuditLog"]] = relationship(back_populates="user", cascade="save-update")
    login_attempts: Mapped[list["LoginAttempt"]] = relationship(
        back_populates="user", cascade="save-update"
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="save-update"
    )

    def set_password(self, password: str) -> None:
        from app.core.security import hash_password

        self.hashed_password = hash_password(password)

    def check_password(self, password: str) -> bool:
        from app.core.security import verify_password

        return verify_password(password, self.hashed_password)


class ProductionLine(Base):
    __tablename__ = "production_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    name: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    cameras: Mapped[list["Camera"]] = relationship(back_populates="line")

    def __repr__(self) -> str:
        return f"<ProductionLine(id={self.id}, name={self.name})>"


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    camera_identity: Mapped[str] = mapped_column(String(255), nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # upload/local_file/webcam/rtsp/simulated
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # RTSP sanitized at API layer
    line_id: Mapped[int | None] = mapped_column(ForeignKey("production_lines.id"), nullable=True, index=True)
    resolution: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fps_input: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="disconnected", nullable=False)
    last_inspection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    line: Mapped["ProductionLine | None"] = relationship(back_populates="cameras")
    sessions: Mapped[list["InspectionSession"]] = relationship(back_populates="camera", cascade="save-update")

    __table_args__ = (Index("ix_cameras_source_type_status", "source_type", "status"),)

    def __repr__(self) -> str:
        return f"<Camera(id={self.id}, name={self.name}, type={self.source_type}, status={self.status})>"


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    name: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    framework: Mapped[str] = mapped_column(String(50), nullable=False)
    framework_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    device: Mapped[str] = mapped_column(String(10), default="cpu", nullable=False)
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="unloaded", nullable=False)
    # Registry metadata (T20)
    task_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    license: Mapped[str | None] = mapped_column(String(200), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classes_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    thresholds_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provenance_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    evaluations: Mapped[list["ModelEvaluation"]] = relationship(back_populates="model_version")
    sessions: Mapped[list["InspectionSession"]] = relationship(back_populates="model_version")


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)  # object_detection / anomaly / runtime
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    hardware: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
    batch_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    model_version: Mapped["ModelVersion"] = relationship(back_populates="evaluations")

    def __repr__(self) -> str:
        return f"<ModelEvaluation(id={self.id}, task={self.task_type})>"


class InspectionSession(Base):
    __tablename__ = "inspection_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), nullable=False, index=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"), nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    camera: Mapped["Camera"] = relationship(back_populates="sessions")
    model_version: Mapped["ModelVersion"] = relationship(back_populates="sessions")
    inspections: Mapped[list["Inspection"]] = relationship(back_populates="session", cascade="save-update")

    def __repr__(self) -> str:
        return f"<InspectionSession(id={self.id}, camera={self.camera_id})>"


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    session_id: Mapped[int] = mapped_column(ForeignKey("inspection_sessions.id"), nullable=False, index=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    detection_results_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    anomaly_score_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[str] = mapped_column(String(20), default="ERROR", nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    session: Mapped["InspectionSession"] = relationship(back_populates="inspections")
    detections: Mapped[list["DetectionResult"]] = relationship(back_populates="inspection", cascade="save-update")
    anomaly: Mapped["AnomalyResult | None"] = relationship(
        back_populates="inspection", uselist=False, cascade="save-update"
    )

    __table_args__ = (
        CheckConstraint(
            "decision IN ('PASS', 'FAIL', 'REVIEW', 'ERROR')",
            name="ck_inspections_decision",
        ),
        Index("ix_inspections_decision_created", "decision", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Inspection(id={self.id}, decision={self.decision})>"


class DetectionResult(Base):
    __tablename__ = "detection_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    area: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    inspection: Mapped["Inspection"] = relationship(back_populates="detections")

    def __repr__(self) -> str:
        return f"<DetectionResult(id={self.id}, label={self.label}, conf={self.confidence})>"


class AnomalyResult(Base):
    __tablename__ = "anomaly_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    inspection_id: Mapped[int] = mapped_column(
        ForeignKey("inspections.id"), nullable=False, index=True, unique=True
    )
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    heatmap_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    inspection: Mapped["Inspection"] = relationship(back_populates="anomaly")

    def __repr__(self) -> str:
        return f"<AnomalyResult(id={self.id}, score={self.score})>"

class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(20), default="info", nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    def __repr__(self) -> str:
        return f"<SystemEvent(id={self.id}, type={self.event_type}, level={self.level})>"


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    user: Mapped["User | None"] = relationship(back_populates="login_attempts")

    def __repr__(self) -> str:
        return f"<LoginAttempt(id={self.id}, username={self.username}, success={self.success})>"


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")

    def __repr__(self) -> str:
        return f"<RefreshToken(id={self.id}, user_id={self.user_id}, revoked={self.revoked})>"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    user: Mapped["User | None"] = relationship(back_populates="audits")

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action={self.action}, user={self.user_id})>"



# ---------------------------------------------------------------------------
# Predictive Maintenance (Phase B) — org-scoped like every tenant table.
# ---------------------------------------------------------------------------


class Machine(Base):
    """A physical machine linked to an inspection station (camera)."""

    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)  # unique per org
    machine_type: Mapped[str] = mapped_column(String(60), nullable=False, default="generic")
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    readings: Mapped[list["SensorReading"]] = relationship(back_populates="machine", cascade="save-update")
    maintenance_requests: Mapped[list["MaintenanceRequest"]] = relationship(
        back_populates="machine", cascade="save-update"
    )

    __table_args__ = (
        Index("uq_machines_org_code", "org_id", "code", unique=True),
        Index("ix_machines_org_status", "org_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Machine(id={self.id}, code={self.code}, status={self.status})>"


class SensorReading(Base):
    """Time-series sensor sample (AI4I 2020 feature schema).

    Retention: rows older than PDM_SENSOR_RETENTION_DAYS are pruned by the
    `pdm_prune_sensor_readings` background task.
    """

    __tablename__ = "sensor_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False, index=True)
    air_temperature_k: Mapped[float] = mapped_column(Float, nullable=False)
    process_temperature_k: Mapped[float] = mapped_column(Float, nullable=False)
    rotational_speed_rpm: Mapped[float] = mapped_column(Float, nullable=False)
    torque_nm: Mapped[float] = mapped_column(Float, nullable=False)
    tool_wear_min: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    machine: Mapped["Machine"] = relationship(back_populates="readings")

    __table_args__ = (
        Index("ix_sensor_readings_machine_time", "machine_id", "recorded_at"),
    )

    def features(self) -> list[float]:
        """Feature vector in the documented, fixed order (shared with training)."""
        return [
            float(self.air_temperature_k),
            float(self.process_temperature_k),
            float(self.rotational_speed_rpm),
            float(self.torque_nm),
            float(self.tool_wear_min),
        ]


FEATURE_NAMES = [
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
]


class MaintenanceRequest(Base):
    """Human-in-the-loop maintenance work item with an approval gate.

    Lifecycle: pending -> approved|rejected -> completed|cancelled.
    ``decided_by_user_id`` is always the identity that approved/rejected.
    """

    __tablename__ = "maintenance_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False, index=True)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    machine: Mapped["Machine"] = relationship(back_populates="maintenance_requests")

    __table_args__ = (
        Index("ix_maintenance_requests_org_status", "org_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<MaintenanceRequest(id={self.id}, machine={self.machine_id}, status={self.status})>"


class MaintenanceSchedule(Base):
    """Recurring preventive-maintenance plan per machine."""

    __tablename__ = "maintenance_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    last_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<MaintenanceSchedule(id={self.id}, machine={self.machine_id})>"
