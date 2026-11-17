"""Add ot_shadow_mode column to cameras for safe factory pilot (dry-run rejector).

Revision ID: a00000000011
Revises: a00000000010
"""

import sqlalchemy as sa
from alembic import op

revision = "a00000000011"
down_revision = "a00000000010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column(
            "ot_shadow_mode",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("cameras", "ot_shadow_mode")
