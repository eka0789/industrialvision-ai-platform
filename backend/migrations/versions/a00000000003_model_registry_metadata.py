"""Add model registry metadata: task, artifact ID, version, license, classes,
thresholds, training-data provenance, and artifact checksum.

Revision ID: a00000000003
Revises: a00000000002
"""

from alembic import op
import sqlalchemy as sa

revision = "a00000000003"
down_revision = "a00000000002"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("task_type", sa.String(length=50)),
    ("artifact_id", sa.String(length=200)),
    ("version", sa.String(length=50)),
    ("license", sa.String(length=200)),
    ("checksum", sa.String(length=64)),
    ("classes_json", sa.JSON()),
    ("thresholds_json", sa.JSON()),
    ("provenance_json", sa.JSON()),
)


def upgrade() -> None:
    with op.batch_alter_table("model_versions", recreate="auto") as batch_op:
        for name, type_ in _COLUMNS:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_versions", recreate="auto") as batch_op:
        for name, _type in reversed(_COLUMNS):
            batch_op.drop_column(name)
