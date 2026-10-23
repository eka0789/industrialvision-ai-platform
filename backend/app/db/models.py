"""Database ORM models for IndustrialVision AI.

Schema surfaces (core):
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
    audits: Mapped[list["AuditLog"]] = relationship(back_populates="user", cascade="save-update")

    def set_password(self, password: str) -> None:
        from app.core.security import hash_password

        self.hashed_password = hash_password(password)

    def check_password(self, password: str) -> bool:
        from app.core.security import verify_password

        return verify_password(password, self.hashed_password)


class ProductionLine(Base):
    __tablename__ = "production_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    framework: Mapped[str] = mapped_column(String(50), nullable=False)
    framework_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    device: Mapped[str] = mapped_column(String(10), default="cpu", nullable=False)
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="unloaded", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), default=now, nullable=False
    )

    evaluations: Mapped[list["ModelEvaluation"]] = relationship(back_populates="model_version")
    sessions: Mapped[list["InspectionSession"]] = relationship(back_populates="model_version")


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(20), default="info", nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    def __repr__(self) -> str:
        return f"<SystemEvent(id={self.id}, type={self.event_type}, level={self.level})>"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

    user: Mapped["User | None"] = relationship(back_populates="audits")

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action={self.action}, user={self.user_id})>"

