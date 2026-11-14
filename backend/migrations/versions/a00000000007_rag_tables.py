"""Knowledge AI / RAG tables (Phase C): rag_documents, rag_chunks.

Embeddings are JSON-encoded universally; similarity is computed by the
storage adapter (pgvector SQL cosine when the extension exists on PostgreSQL,
bounded numpy scan otherwise — see app/rag/storage.py).

Revision ID: a00000000007
Revises: a00000000006
"""

import sqlalchemy as sa
from alembic import op

revision = "a00000000007"
down_revision = "a00000000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("embedding_provider", sa.String(60), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rag_documents_org_id", "rag_documents", ["org_id"])
    op.create_index("ix_rag_documents_sha256", "rag_documents", ["sha256"])
    op.create_index("ix_rag_documents_status", "rag_documents", ["status"])
    op.create_index("ix_rag_documents_org_status", "rag_documents", ["org_id", "status"])

    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, server_default="1"),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("rag_documents.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding_json", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rag_chunks_org_id", "rag_chunks", ["org_id"])
    op.create_index("ix_rag_chunks_document_id", "rag_chunks", ["document_id"])


def downgrade() -> None:
    op.drop_table("rag_chunks")
    op.drop_table("rag_documents")
