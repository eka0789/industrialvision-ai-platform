"""AI Operations Agent tables (Phase D): agent_runs, agent_tool_logs,
agent_approvals. All org-scoped.

Revision ID: a00000000008
Revises: a00000000007
"""

import sqlalchemy as sa
from alembic import op

revision = "a00000000008"
down_revision = "a00000000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False, server_default="mock-demo"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_runs_org_id", "agent_runs", ["org_id"])
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])

    op.create_table(
        "agent_tool_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("args_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_tool_logs_run_id", "agent_tool_logs", ["run_id"])

    op.create_table(
        "agent_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("args_json", sa.JSON(), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_approvals_org_id", "agent_approvals", ["org_id"])
    op.create_index("ix_agent_approvals_requested_by_user_id", "agent_approvals", ["requested_by_user_id"])
    op.create_index("ix_agent_approvals_status", "agent_approvals", ["status"])


def downgrade() -> None:
    op.drop_table("agent_approvals")
    op.drop_table("agent_tool_logs")
    op.drop_table("agent_runs")
