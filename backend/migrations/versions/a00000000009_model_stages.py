"""Model registry stage lifecycle (E1): stage + previous_stage + drift
reference stats (E2) on model_versions.

Revision ID: a00000000009
Revises: a00000000008
"""

import sqlalchemy as sa
from alembic import op

revision = "a00000000009"
down_revision = "a00000000008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_versions") as batch:
        batch.add_column(sa.Column("stage", sa.String(20), nullable=True))
        batch.add_column(sa.Column("previous_stage", sa.String(20), nullable=True))
        batch.add_column(sa.Column("reference_stats_json", sa.JSON(), nullable=True))
    op.execute("UPDATE model_versions SET stage = 'development' WHERE stage IS NULL")
    with op.batch_alter_table("model_versions") as batch:
        batch.alter_column("stage", existing_type=sa.String(20), nullable=False, server_default="development")
    op.create_index("ix_model_versions_stage", "model_versions", ["stage"])


def downgrade() -> None:
    op.drop_index("ix_model_versions_stage", table_name="model_versions")
    with op.batch_alter_table("model_versions") as batch:
        batch.drop_column("reference_stats_json")
        batch.drop_column("previous_stage")
        batch.drop_column("stage")
