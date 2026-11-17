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
    # Phase 4 Industrial OT Bridge & PLC Rejector I/O configuration
    ot_protocol: Mapped[str] = mapped_column(String(30), default="simulated", nullable=False, server_default="simulated")
    ot_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ot_port: Mapped[int | None] = mapped_column(Integer, nullable=True, default=502)
    trigger_register: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    reject_coil: Mapped[int | None] = mapped_column(Integer, nullable=True, default=100)
    reject_delay_ms: Mapped[int] = mapped_column(Integer, default=120, nullable=False, server_default="120")
    reject_pulse_ms: Mapped[int] = mapped_column(Integer, default=45, nullable=False, server_default="45")
    ot_shadow_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
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
    # Stage lifecycle (E1): development → staging → production; rollback keeps
    # the previous stage so reverts are explicit, not guessed.
    stage: Mapped[str] = mapped_column(String(20), default="development", nullable=False, server_default="development")
    previous_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reference_stats_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # E2 training-time drift reference
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
    batch_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    defect_category: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
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
    # Food Division packaging / thermal machine parameters (optional, nullable for AI4I compatibility)
    sealer_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    pneumatic_pressure_bar: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    conveyor_speed_mpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    motor_current_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    vibration_mm_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    ambient_humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    machine: Mapped["Machine"] = relationship(back_populates="readings")

    __table_args__ = (
        Index("ix_sensor_readings_machine_time", "machine_id", "recorded_at"),
    )

    @property
    def motor_current_amp(self) -> float | None:
        return self.motor_current_a

    @motor_current_amp.setter
    def motor_current_amp(self, value: float | None) -> None:
        self.motor_current_a = value

    def features(self) -> list[float]:
        """Feature vector in the documented, fixed order (shared with training)."""
        return [
            float(self.air_temperature_k),
            float(self.process_temperature_k),
            float(self.rotational_speed_rpm),
            float(self.torque_nm),
            float(self.tool_wear_min),
        ]

    def food_features(self) -> dict[str, float | None]:
        """Food packaging & oven parameters when present."""
        return {
            "sealer_temp_c": float(self.sealer_temp_c) if self.sealer_temp_c is not None else None,
            "pneumatic_pressure_bar": float(self.pneumatic_pressure_bar) if self.pneumatic_pressure_bar is not None else None,
            "zone_temp_c": float(self.zone_temp_c) if self.zone_temp_c is not None else None,
            "conveyor_speed_mpm": float(self.conveyor_speed_mpm) if self.conveyor_speed_mpm is not None else None,
            "motor_current_a": float(self.motor_current_a) if self.motor_current_a is not None else None,
            "motor_current_amp": float(self.motor_current_a) if self.motor_current_a is not None else None,
            "vibration_mm_s": float(self.vibration_mm_s) if self.vibration_mm_s is not None else None,
            "ambient_humidity_pct": float(self.ambient_humidity_pct) if self.ambient_humidity_pct is not None else None,
        }


FEATURE_NAMES = [
    "air_temperature_k",
    "process_temperature_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
]

FOOD_FEATURE_NAMES = [
    "sealer_temp_c",
    "pneumatic_pressure_bar",
    "conveyor_speed_mpm",
    "motor_current_amp",
    "vibration_mm_s",
    "ambient_humidity_pct",
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


# ---------------------------------------------------------------------------
# Knowledge AI / RAG (Phase C) — org-scoped.
# ---------------------------------------------------------------------------


class RagDocument(Base):
    """An ingested knowledge document (PDF/TXT/DOCX/CSV)."""

    __tablename__ = "rag_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    chunks: Mapped[list["RagChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_rag_documents_org_status", "org_id", "status"),)

    def __repr__(self) -> str:
        return f"<RagDocument(id={self.id}, status={self.status}, chunks={self.chunk_count})>"


class RagChunk(Base):
    """A chunk of a document with its embedding vector.

    The embedding is stored JSON-encoded (list of floats) for both SQLite and
    PostgreSQL deployments; vector similarity is computed in the storage
    adapter (``app/rag/storage.py``) which uses pgvector SQL cosine when the
    extension is available on PostgreSQL and a bounded numpy scan otherwise.
    """

    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    document_id: Mapped[int] = mapped_column(ForeignKey("rag_documents.id"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    document: Mapped["RagDocument"] = relationship(back_populates="chunks")

    def __repr__(self) -> str:
        return f"<RagChunk(id={self.id}, document={self.document_id}, ordinal={self.ordinal})>"


# ---------------------------------------------------------------------------
# AI Operations Agent (Phase D) — org-scoped.
# ---------------------------------------------------------------------------


class AgentRun(Base):
    """One agent conversation turn: question → plan → tool logs → response."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False, index=True)
    plan_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(40), default="mock-demo", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    tool_logs: Mapped[list["AgentToolLog"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<AgentRun(id={self.id}, status={self.status})>"


class AgentToolLog(Base):
    """Structured execution log for one tool invocation inside a run."""

    __tablename__ = "agent_tool_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    args_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ok", nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    run: Mapped["AgentRun"] = relationship(back_populates="tool_logs")

    def __repr__(self) -> str:
        return f"<AgentToolLog(id={self.id}, tool={self.tool_name}, status={self.status})>"


class AgentApproval(Base):
    """Human-in-the-loop approval for side-effecting agent tools (D3).

    Lifecycle: pending → approved|rejected|expired → executed|failed.
    ``decided_by_user_id`` is the approving human; the tool then EXECUTES with
    that identity — the requester can never self-approve (enforced in the API
    layer and asserted in tests).
    """

    __tablename__ = "agent_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    args_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    def __repr__(self) -> str:
        return f"<AgentApproval(id={self.id}, tool={self.tool_name}, status={self.status})>"


# ---------------------------------------------------------------------------
# Food Division Enhancements (ProductSKU, SKU Recipe, Production Batch, OT Bridge)
# ---------------------------------------------------------------------------


class ProductSKU(Base):
    """Food Division Product SKU master entity (multi-tenant)."""

    __tablename__ = "product_skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    sku_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(60), default="snack", nullable=False, server_default="snack")
    packaging_type: Mapped[str] = mapped_column(String(80), default="flow_wrap", nullable=False, server_default="flow_wrap")
    expiry_format: Mapped[str | None] = mapped_column(String(160), nullable=True, default=r"(?:EXP|BB|LOT)?\s*\d{2}[/.\-]\d{2}[/.\-]\d{2,4}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    recipes: Mapped[list["InspectionRecipe"]] = relationship(back_populates="sku", cascade="save-update")

    __table_args__ = (
        Index("uq_product_skus_org_code", "org_id", "sku_code", unique=True),
        Index("ix_product_skus_org_category", "org_id", "category"),
    )

    def __repr__(self) -> str:
        return f"<ProductSKU(id={self.id}, sku_code={self.sku_code}, category={self.category})>"


class InspectionRecipe(Base):
    """Product SKU inspection recipe with Multi-Zone ROI and preprocessing config.

    Enforces atomic activation: at most 1 active recipe per (org_id, camera_id).
    """

    __tablename__ = "inspection_recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    sku_id: Mapped[int | None] = mapped_column(ForeignKey("product_skus.id"), nullable=True, index=True)
    sku_code: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("production_lines.id"), nullable=True, index=True)
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id"), nullable=True, index=True)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pass_sample_rate: Mapped[int] = mapped_column(Integer, default=500, nullable=False, server_default="500")
    preprocessing_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    roi_zones: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    sku: Mapped["ProductSKU | None"] = relationship(back_populates="recipes")

    __table_args__ = (
        Index("ix_inspection_recipes_org_active", "org_id", "is_active"),
        Index("ix_inspection_recipes_org_camera_active", "org_id", "camera_id", "is_active"),
    )

    @property
    def preprocessing_json(self) -> dict | None:
        return self.preprocessing_config

    @preprocessing_json.setter
    def preprocessing_json(self, value: dict | None) -> None:
        self.preprocessing_config = value

    @property
    def zones_json(self) -> list | None:
        return self.roi_zones

    @zones_json.setter
    def zones_json(self, value: list | None) -> None:
        self.roi_zones = value

    def __repr__(self) -> str:
        return f"<InspectionRecipe(id={self.id}, name={self.name}, sku_id={self.sku_id}, active={self.is_active})>"


class ProductionBatch(Base):
    """Food safety traceability batch/lot with shift tracking and QA digital signature (Fase 2)."""

    __tablename__ = "production_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    batch_code: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    sku_id: Mapped[int | None] = mapped_column(ForeignKey("product_skus.id"), nullable=True, index=True)
    sku_code: Mapped[str] = mapped_column(String(80), default="FOOD-SKU", nullable=False, index=True)
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("inspection_recipes.id"), nullable=True, index=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("production_lines.id"), nullable=True, index=True)
    line_name: Mapped[str | None] = mapped_column(String(120), nullable=True, default="Line 01")
    shift: Mapped[str] = mapped_column(String(40), default="SHIFT_1", nullable=False)
    started_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False, index=True)
    target_quantity: Mapped[int] = mapped_column(Integer, default=1000, nullable=False, server_default="1000")
    total_inspected: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    total_pass: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    total_fail: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    total_review: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    defect_counts_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    qa_signed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    qa_signature_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qa_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qa_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    __table_args__ = (
        Index("uq_production_batches_org_code", "org_id", "batch_code", unique=True),
        Index("ix_production_batches_org_status", "org_id", "status"),
    )

    @property
    def operator_id(self) -> int | None:
        return self.started_by

    @operator_id.setter
    def operator_id(self, value: int | None) -> None:
        self.started_by = value

    @property
    def target_qty(self) -> int:
        return self.target_quantity

    @target_qty.setter
    def target_qty(self, value: int) -> None:
        self.target_quantity = value

    @property
    def ended_at(self) -> datetime | None:
        return self.closed_at

    @ended_at.setter
    def ended_at(self, value: datetime | None) -> None:
        self.closed_at = value

    def __repr__(self) -> str:
        return f"<ProductionBatch(id={self.id}, code={self.batch_code}, status={self.status})>"


class OTTriggerEvent(Base):
    """Operational Technology (OT) photoelectric trigger and PLC rejector actuation record."""

    __tablename__ = "ot_trigger_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True, server_default="1"
    )
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), nullable=False, index=True)
    inspection_id: Mapped[int | None] = mapped_column(ForeignKey("inspections.id"), nullable=True, index=True)
    protocol: Mapped[str] = mapped_column(String(30), default="simulated", nullable=False)
    sensor_id: Mapped[str] = mapped_column(String(80), default="PHOTO-SENS-01", nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    rejector_actuated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    actuator_type: Mapped[str] = mapped_column(String(40), default="pneumatic_air_blast", nullable=False)
    plc_target: Mapped[str | None] = mapped_column(String(160), nullable=True)
    inference_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cycle_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    __table_args__ = (
        Index("ix_ot_trigger_events_org_created", "org_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<OTTriggerEvent(id={self.id}, decision={self.decision}, actuated={self.rejector_actuated})>"
