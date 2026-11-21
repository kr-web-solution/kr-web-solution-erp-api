"""034 deleted_invoices archive table for hard-deleted invoices

Revision ID: q4r5s6t7u8v9
Revises: p3q4r5s6t7u8
Create Date: 2026-11-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q4r5s6t7u8v9"
down_revision: Union[str, Sequence[str], None] = "p3q4r5s6t7u8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "deleted_invoices" in tables:
        return

    op.create_table(
        "deleted_invoices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("original_invoice_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=False),
        sa.Column("invoice_type", sa.String(length=20), nullable=True),
        sa.Column("ledger", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("client_id", sa.String(length=36), nullable=True),
        sa.Column("client_name", sa.String(length=255), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("project_name", sa.String(length=255), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("subtotal_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tax_rate_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("tax_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("gst_enabled", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
        sa.Column("deleted_by_email", sa.String(length=255), nullable=True),
        sa.Column(
            "deleted_reason",
            sa.String(length=40),
            nullable=False,
            server_default="user_delete",
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["deleted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_deleted_invoices_id"), "deleted_invoices", ["id"], unique=False
    )
    op.create_index(
        "ix_deleted_invoices_invoice_number",
        "deleted_invoices",
        ["invoice_number"],
        unique=False,
    )
    op.create_index(
        "ix_deleted_invoices_deleted_at",
        "deleted_invoices",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_deleted_invoices_original_id",
        "deleted_invoices",
        ["original_invoice_id"],
        unique=False,
    )
    op.create_index(
        "ix_deleted_invoices_client_id",
        "deleted_invoices",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_deleted_invoices_deleted_by"),
        "deleted_invoices",
        ["deleted_by"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "deleted_invoices" not in tables:
        return
    op.drop_index(op.f("ix_deleted_invoices_deleted_by"), table_name="deleted_invoices")
    op.drop_index("ix_deleted_invoices_client_id", table_name="deleted_invoices")
    op.drop_index("ix_deleted_invoices_original_id", table_name="deleted_invoices")
    op.drop_index("ix_deleted_invoices_deleted_at", table_name="deleted_invoices")
    op.drop_index("ix_deleted_invoices_invoice_number", table_name="deleted_invoices")
    op.drop_index(op.f("ix_deleted_invoices_id"), table_name="deleted_invoices")
    op.drop_table("deleted_invoices")
