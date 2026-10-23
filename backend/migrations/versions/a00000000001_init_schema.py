"""Initial schema migration for IndustrialVision AI."""

from alembic import op
import sqlalchemy as sa

revision = "a00000000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(40), unique=True, nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(80), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(255), unique=True, nullable=True, index=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role_id", sa.Integer, sa.ForeignKey("roles.id"), nullable=False, index=True),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "production_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), unique=True, nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, index=True),
        sa.Column("camera_identity", sa.String(255), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("line_id", sa.Integer, sa.ForeignKey("production_lines.id"), nullable=True, index=True),
        sa.Column("resolution", sa.String(40), nullable=True),
        sa.Column("fps_input", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), default="disconnected", nullable=False),
        sa.Column("last_inspection_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_cameras_source_type_status", "source_type", "status"),
    )
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), unique=True, nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("framework", sa.String(50), nullable=False),
        sa.Column("framework_version", sa.String(50), nullable=True),
        sa.Column("source_path", sa.Text, nullable=True),
        sa.Column("device", sa.String(10), default="cpu", nullable=False),
        sa.Column("config_json", sa.JSON, nullable=True),
        sa.Column("is_active", sa.Boolean, default=False, nullable=False),
        sa.Column("status", sa.String(20), default="unloaded", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "model_evaluations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("model_version_id", sa.Integer, sa.ForeignKey("model_versions.id"), nullable=False, index=True),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("metrics_json", sa.JSON, nullable=True),
        sa.Column("hardware", sa.Text, nullable=True),
        sa.Column("input_size", sa.String(50), nullable=True),
        sa.Column("batch_size", sa.Integer, nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "inspection_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("camera_id", sa.Integer, sa.ForeignKey("cameras.id"), nullable=False, index=True),
        sa.Column("model_version_id", sa.Integer, sa.ForeignKey("model_versions.id"), nullable=False, index=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), default="running", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "inspections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("inspection_sessions.id"), nullable=False, index=True),
        sa.Column("camera_id", sa.Integer, sa.ForeignKey("cameras.id"), nullable=False, index=True),
        sa.Column("product_id", sa.String(200), nullable=True),
        sa.Column("batch_id", sa.String(200), nullable=True),
        sa.Column("model_name", sa.String(200), nullable=True),
        sa.Column("model_version", sa.String(50), nullable=True),
        sa.Column("detection_results_json", sa.JSON, nullable=True),
        sa.Column("anomaly_score_json", sa.JSON, nullable=True),
        sa.Column("decision", sa.String(20), default="ERROR", nullable=False),
        sa.Column("latency_ms", sa.Float, nullable=True),
        sa.Column("evidence_path", sa.Text, nullable=True),
        sa.Column("error_status", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_inspections_decision_created", "decision", "created_at"),
    )
    op.create_table(
        "detection_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("inspection_id", sa.Integer, sa.ForeignKey("inspections.id"), nullable=False, index=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("bbox_json", sa.JSON, nullable=True),
        sa.Column("area", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "anomaly_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "inspection_id",
            sa.Integer,
            sa.ForeignKey("inspections.id"),
            nullable=False,
            index=True,
            unique=True,
        ),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("heatmap_path", sa.Text, nullable=True),
        sa.Column("threshold", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_type", sa.String(60), nullable=False, index=True),
        sa.Column("level", sa.String(20), default="info", nullable=False),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("action", sa.String(80), nullable=False, index=True),
        sa.Column("details_json", sa.JSON, nullable=True),
        sa.Column("ip_address", sa.String(60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("audit_logs")
    op.drop_table("system_events")
    op.drop_table("anomaly_results")
    op.drop_table("detection_results")
    op.drop_table("inspections")
    op.drop_table("inspection_sessions")
    op.drop_table("model_evaluations")
    op.drop_table("model_versions")
    op.drop_table("cameras")
    op.drop_table("production_lines")
    op.drop_table("users")
    op.drop_table("roles")

