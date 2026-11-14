"""Predictive Maintenance tables (Phase B): machines, sensor_readings,
maintenance_requests, maintenance_schedules. All org-scoped.

Revision ID: a00000000006
Revises: a00000000005
"""

import sqlalchemy as sa
from alembic import op

revision = "a00000000006"
down_revision = "a00000000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("machine_type", sa.String(60), nullable=False, server_default="generic"),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("cameras.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_machines_org_id", "machines", ["org_id"])
    op.create_index("ix_machines_name", "machines", ["name"])
    op.create_index("ix_machines_code", "machines", ["code"])
    op.create_index("ix_machines_camera_id", "machines", ["camera_id"])
    op.create_index("uq_machines_org_code", "machines", ["org_id", "code"], unique=True)
    op.create_index("ix_machines_org_status", "machines", ["org_id", "status"])

    op.create_table(
        "sensor_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("machine_id", sa.Integer(), sa.ForeignKey("machines.id"), nullable=False),
        sa.Column("air_temperature_k", sa.Float(), nullable=False),
        sa.Column("process_temperature_k", sa.Float(), nullable=False),
        sa.Column("rotational_speed_rpm", sa.Float(), nullable=False),
        sa.Column("torque_nm", sa.Float(), nullable=False),
        sa.Column("tool_wear_min", sa.Float(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sensor_readings_org_id", "sensor_readings", ["org_id"])
    op.create_index("ix_sensor_readings_machine_id", "sensor_readings", ["machine_id"])
    op.create_index("ix_sensor_readings_machine_time", "sensor_readings", ["machine_id", "recorded_at"])

    op.create_table(
        "maintenance_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("machine_id", sa.Integer(), sa.ForeignKey("machines.id"), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_maintenance_requests_org_id", "maintenance_requests", ["org_id"])
    op.create_index("ix_maintenance_requests_machine_id", "maintenance_requests", ["machine_id"])
    op.create_index("ix_maintenance_requests_requested_by_user_id", "maintenance_requests", ["requested_by_user_id"])
    op.create_index("ix_maintenance_requests_status", "maintenance_requests", ["status"])
    op.create_index("ix_maintenance_requests_org_status", "maintenance_requests", ["org_id", "status"])

    op.create_table(
        "maintenance_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("machine_id", sa.Integer(), sa.ForeignKey("machines.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("last_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_maintenance_schedules_org_id", "maintenance_schedules", ["org_id"])
    op.create_index("ix_maintenance_schedules_machine_id", "maintenance_schedules", ["machine_id"])


def downgrade() -> None:
    op.drop_table("maintenance_schedules")
    op.drop_table("maintenance_requests")
    op.drop_table("sensor_readings")
    op.drop_table("machines")
