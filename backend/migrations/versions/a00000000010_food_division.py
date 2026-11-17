"""Food Division tables & sensor columns: product_skus, inspection_recipes,
production_batches, ot_trigger_events, defect_category on inspections,
and food packaging sensor fields on sensor_readings.

Revision ID: a00000000010
Revises: a00000000009
"""

import sqlalchemy as sa
from alembic import op

revision = "a00000000010"
down_revision = "a00000000009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_skus",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("sku_code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("category", sa.String(60), nullable=False, server_default="snack"),
        sa.Column("packaging_type", sa.String(80), nullable=False, server_default="flow_wrap"),
        sa.Column("expiry_format", sa.String(160), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_product_skus_org_id", "product_skus", ["org_id"])
    op.create_index("ix_product_skus_sku_code", "product_skus", ["sku_code"])
    op.create_index("uq_product_skus_org_code", "product_skus", ["org_id", "sku_code"], unique=True)
    op.create_index("ix_product_skus_org_category", "product_skus", ["org_id", "category"])

    op.create_table(
        "inspection_recipes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("product_skus.id"), nullable=True),
        sa.Column("sku_code", sa.String(80), nullable=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("production_lines.id"), nullable=True),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("cameras.id"), nullable=True),
        sa.Column("model_version_id", sa.Integer(), sa.ForeignKey("model_versions.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("pass_sample_rate", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("preprocessing_config", sa.JSON(), nullable=True),
        sa.Column("roi_zones", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_inspection_recipes_org_id", "inspection_recipes", ["org_id"])
    op.create_index("ix_inspection_recipes_sku_id", "inspection_recipes", ["sku_id"])
    op.create_index("ix_inspection_recipes_sku_code", "inspection_recipes", ["sku_code"])
    op.create_index("ix_inspection_recipes_line_id", "inspection_recipes", ["line_id"])
    op.create_index("ix_inspection_recipes_camera_id", "inspection_recipes", ["camera_id"])
    op.create_index("ix_inspection_recipes_model_version_id", "inspection_recipes", ["model_version_id"])
    op.create_index("ix_inspection_recipes_org_active", "inspection_recipes", ["org_id", "is_active"])
    op.create_index("ix_inspection_recipes_org_camera_active", "inspection_recipes", ["org_id", "camera_id", "is_active"])

    op.create_table(
        "production_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("batch_code", sa.String(120), nullable=False),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("product_skus.id"), nullable=True),
        sa.Column("sku_code", sa.String(80), nullable=False, server_default="FOOD-SKU"),
        sa.Column("recipe_id", sa.Integer(), sa.ForeignKey("inspection_recipes.id"), nullable=True),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("production_lines.id"), nullable=True),
        sa.Column("line_name", sa.String(120), nullable=True, server_default="Line 01"),
        sa.Column("shift", sa.String(40), nullable=False, server_default="SHIFT_1"),
        sa.Column("started_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("target_quantity", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("total_inspected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_pass", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_fail", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_review", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("defect_counts_json", sa.JSON(), nullable=True),
        sa.Column("qa_signed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("qa_signature_hash", sa.String(64), nullable=True),
        sa.Column("qa_signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qa_notes", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_production_batches_org_id", "production_batches", ["org_id"])
    op.create_index("ix_production_batches_batch_code", "production_batches", ["batch_code"])
    op.create_index("ix_production_batches_sku_id", "production_batches", ["sku_id"])
    op.create_index("ix_production_batches_sku_code", "production_batches", ["sku_code"])
    op.create_index("ix_production_batches_recipe_id", "production_batches", ["recipe_id"])
    op.create_index("ix_production_batches_line_id", "production_batches", ["line_id"])
    op.create_index("ix_production_batches_started_by", "production_batches", ["started_by"])
    op.create_index("ix_production_batches_status", "production_batches", ["status"])
    op.create_index("uq_production_batches_org_code", "production_batches", ["org_id", "batch_code"], unique=True)
    op.create_index("ix_production_batches_org_status", "production_batches", ["org_id", "status"])

    op.create_table(
        "ot_trigger_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("inspection_id", sa.Integer(), sa.ForeignKey("inspections.id"), nullable=True),
        sa.Column("protocol", sa.String(30), nullable=False, server_default="simulated"),
        sa.Column("sensor_id", sa.String(80), nullable=False, server_default="PHOTO-SENS-01"),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("rejector_actuated", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("actuator_type", sa.String(40), nullable=False, server_default="pneumatic_air_blast"),
        sa.Column("plc_target", sa.String(160), nullable=True),
        sa.Column("inference_latency_ms", sa.Float(), nullable=True),
        sa.Column("total_cycle_ms", sa.Float(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ot_trigger_events_org_id", "ot_trigger_events", ["org_id"])
    op.create_index("ix_ot_trigger_events_camera_id", "ot_trigger_events", ["camera_id"])
    op.create_index("ix_ot_trigger_events_inspection_id", "ot_trigger_events", ["inspection_id"])
    op.create_index("ix_ot_trigger_events_org_created", "ot_trigger_events", ["org_id", "created_at"])

    with op.batch_alter_table("cameras") as batch:
        batch.add_column(sa.Column("ot_protocol", sa.String(30), nullable=False, server_default="simulated"))
        batch.add_column(sa.Column("ot_host", sa.String(255), nullable=True))
        batch.add_column(sa.Column("ot_port", sa.Integer(), nullable=True, server_default="502"))
        batch.add_column(sa.Column("trigger_register", sa.Integer(), nullable=True, server_default="1"))
        batch.add_column(sa.Column("reject_coil", sa.Integer(), nullable=True, server_default="100"))
        batch.add_column(sa.Column("reject_delay_ms", sa.Integer(), nullable=False, server_default="120"))
        batch.add_column(sa.Column("reject_pulse_ms", sa.Integer(), nullable=False, server_default="45"))

    with op.batch_alter_table("inspections") as batch:
        batch.add_column(sa.Column("defect_category", sa.String(80), nullable=True))
    op.create_index("ix_inspections_batch_id", "inspections", ["batch_id"])
    op.create_index("ix_inspections_defect_category", "inspections", ["defect_category"])

    with op.batch_alter_table("sensor_readings") as batch:
        batch.add_column(sa.Column("sealer_temp_c", sa.Float(), nullable=True))
        batch.add_column(sa.Column("pneumatic_pressure_bar", sa.Float(), nullable=True))
        batch.add_column(sa.Column("zone_temp_c", sa.Float(), nullable=True))
        batch.add_column(sa.Column("conveyor_speed_mpm", sa.Float(), nullable=True))
        batch.add_column(sa.Column("motor_current_a", sa.Float(), nullable=True))
        batch.add_column(sa.Column("vibration_mm_s", sa.Float(), nullable=True))
        batch.add_column(sa.Column("ambient_humidity_pct", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sensor_readings") as batch:
        batch.drop_column("ambient_humidity_pct")
        batch.drop_column("vibration_mm_s")
        batch.drop_column("motor_current_a")
        batch.drop_column("conveyor_speed_mpm")
        batch.drop_column("zone_temp_c")
        batch.drop_column("pneumatic_pressure_bar")
        batch.drop_column("sealer_temp_c")

    op.drop_index("ix_inspections_defect_category", table_name="inspections")
    op.drop_index("ix_inspections_batch_id", table_name="inspections")
    with op.batch_alter_table("inspections") as batch:
        batch.drop_column("defect_category")

    with op.batch_alter_table("cameras") as batch:
        batch.drop_column("reject_pulse_ms")
        batch.drop_column("reject_delay_ms")
        batch.drop_column("reject_coil")
        batch.drop_column("trigger_register")
        batch.drop_column("ot_port")
        batch.drop_column("ot_host")
        batch.drop_column("ot_protocol")

    op.drop_table("ot_trigger_events")
    op.drop_table("production_batches")
    op.drop_table("inspection_recipes")
    op.drop_table("product_skus")
