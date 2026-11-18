"""011_project_documents

Quotation, agreement, and other files stored on a project.

Revision ID: k7f8a9b0c1d2
Revises: j6e7f8a9b0c1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "j6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "project_documents" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "project_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("doc_kind", sa.String(length=30), server_default="other", nullable=False),
        sa.Column("uploaded_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_documents_id", "project_documents", ["id"])
    op.create_index("ix_project_documents_project_id", "project_documents", ["project_id"])
    op.create_index("ix_project_documents_doc_kind", "project_documents", ["doc_kind"])


def downgrade() -> None:
    op.drop_index("ix_project_documents_doc_kind", table_name="project_documents")
    op.drop_index("ix_project_documents_project_id", table_name="project_documents")
    op.drop_index("ix_project_documents_id", table_name="project_documents")
    op.drop_table("project_documents")
