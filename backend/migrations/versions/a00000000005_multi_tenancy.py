"""Multi-tenancy: organizations table + org_id on every tenant-scoped table.

Row id 1 is the bootstrap organization ("Default Organization"); every
existing row lands there via server_default + explicit backfill so a
single-tenant install keeps working unchanged. Cross-tenant isolation is
enforced in the query layer (app/services/tenant.py) and asserted by router
tests.

Uses ``batch_alter_table`` so the recreate-based column changes also run on
SQLite (upgrade/downgrade/re-upgrade verified on SQLite; PostgreSQL is covered
by the CI integration job).

Revision ID: a00000000005
Revises: a00000000004
"""

import sqlalchemy as sa
from alembic import op

revision = "a00000000005"
down_revision = "a00000000004"
branch_labels = None
depends_on = None

# Tables that gain a NOT NULL org_id (tenant data).
TENANT_TABLES = [
    "users",
    "production_lines",
    "cameras",
    "model_versions",
    "model_evaluations",
    "inspection_sessions",
    "inspections",
    "detection_results",
    "anomaly_results",
    "system_events",
    "login_attempts",
    "refresh_tokens",
    "audit_logs",
]


def _add_org_column(table: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.add_column(
            sa.Column(
                "org_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id", name=f"fk_{table}_org_id"),
                nullable=True,
            )
        )


def _backfill_and_harden(table: str) -> None:
    op.execute(f"UPDATE {table} SET org_id = 1 WHERE org_id IS NULL")
    with op.batch_alter_table(table) as batch:
        batch.alter_column("org_id", existing_type=sa.Integer(), nullable=False)


def _index_and_default(table: str) -> None:
    op.create_index(f"ix_{table}_org_id", table, ["org_id"])
    with op.batch_alter_table(table) as batch:
        batch.alter_column(
            "org_id",
            existing_type=sa.Integer(),
            server_default="1",
            existing_nullable=False,
        )


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False, unique=True),
        sa.Column("slug", sa.String(80), nullable=False, unique=True, index=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        "INSERT INTO organizations (id, name, slug, is_active) "
        "VALUES (1, 'Default Organization', 'default', 1)"
    )

    for table in TENANT_TABLES:
        _add_org_column(table)
    for table in TENANT_TABLES:
        _backfill_and_harden(table)
    for table in TENANT_TABLES:
        _index_and_default(table)


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.drop_index(f"ix_{table}_org_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("org_id")
    op.drop_table("organizations")
