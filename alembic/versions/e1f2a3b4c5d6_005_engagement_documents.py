"""005_engagement_documents

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-06 14:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "engagement_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("engagement_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), server_default="application/octet-stream", nullable=False),
        sa.Column("size_bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("doc_kind", sa.String(length=30), server_default="enquiry", nullable=False),
        sa.Column("uploaded_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_engagement_documents_id"), "engagement_documents", ["id"], unique=False)
    op.create_index(op.f("ix_engagement_documents_engagement_id"), "engagement_documents", ["engagement_id"], unique=False)
    op.create_index(op.f("ix_engagement_documents_doc_kind"), "engagement_documents", ["doc_kind"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_engagement_documents_doc_kind"), table_name="engagement_documents")
    op.drop_index(op.f("ix_engagement_documents_engagement_id"), table_name="engagement_documents")
    op.drop_index(op.f("ix_engagement_documents_id"), table_name="engagement_documents")
    op.drop_table("engagement_documents")
