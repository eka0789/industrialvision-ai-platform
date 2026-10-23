"""Align camera FPS type and constrain inspection decisions."""

from alembic import op
import sqlalchemy as sa

revision = "a00000000002"
down_revision = "a00000000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("cameras", recreate="always") as batch_op:
            batch_op.alter_column(
                "fps_input",
                existing_type=sa.String(length=20),
                type_=sa.Float(),
                existing_nullable=True,
            )
    else:
        op.alter_column(
            "cameras",
            "fps_input",
            existing_type=sa.String(length=20),
            type_=sa.Float(),
            existing_nullable=True,
            postgresql_using="NULLIF(fps_input, '')::double precision",
        )

    with op.batch_alter_table("inspections") as batch_op:
        batch_op.create_check_constraint(
            "ck_inspections_decision",
            "decision IN ('PASS', 'FAIL', 'REVIEW', 'ERROR')",
        )


def downgrade() -> None:
    with op.batch_alter_table("inspections") as batch_op:
        batch_op.drop_constraint("ck_inspections_decision", type_="check")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("cameras", recreate="always") as batch_op:
            batch_op.alter_column(
                "fps_input",
                existing_type=sa.Float(),
                type_=sa.String(length=20),
                existing_nullable=True,
            )
    else:
        op.alter_column(
            "cameras",
            "fps_input",
            existing_type=sa.Float(),
            type_=sa.String(length=20),
            existing_nullable=True,
        )
