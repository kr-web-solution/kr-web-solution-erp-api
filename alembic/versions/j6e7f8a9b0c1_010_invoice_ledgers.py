"""010_invoice_ledgers

Existing invoices stay. New columns classify them so project totals can be
derived. Agreed project terms (budget_paise, advance_paise, development_days,
recurring amount) are not dropped.

Revision ID: j6e7f8a9b0c1
Revises: i5d6e7f8a9b0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "j6e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "i5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("ledger", sa.String(length=20), nullable=False, server_default="development"))
    op.add_column("invoices", sa.Column("period_months", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("invoices", sa.Column("covers_from", sa.Date(), nullable=True))
    op.add_column("invoices", sa.Column("covers_to", sa.Date(), nullable=True))
    op.add_column("invoices", sa.Column("monthly_rate_paise", sa.BigInteger(), nullable=True))
    op.create_index("ix_invoices_ledger", "invoices", ["ledger"])

    op.execute(
        """
        UPDATE invoices SET ledger = 'maintenance', invoice_type = 'amc'
        WHERE invoice_type = 'maintenance'
        """
    )
    op.execute(
        """
        UPDATE invoices SET invoice_type = 'advance', ledger = 'development'
        WHERE id IN (SELECT advance_invoice_id FROM projects WHERE advance_invoice_id IS NOT NULL)
           OR lower(coalesce(notes, '')) LIKE '%advance%'
        """
    )
    op.execute(
        """
        UPDATE invoices SET invoice_type = 'balance', ledger = 'development'
        WHERE id IN (SELECT balance_invoice_id FROM projects WHERE balance_invoice_id IS NOT NULL)
        """
    )
    op.execute(
        """
        UPDATE invoices SET invoice_type = 'custom_dev', ledger = 'development'
        WHERE invoice_type = 'project'
        """
    )

    op.create_table(
        "invoice_audit_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("invoice_id", sa.String(length=36), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("old_subtotal_paise", sa.BigInteger(), nullable=True),
        sa.Column("new_subtotal_paise", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoice_audit_logs_invoice_id", "invoice_audit_logs", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_audit_logs_invoice_id", table_name="invoice_audit_logs")
    op.drop_table("invoice_audit_logs")
    op.drop_index("ix_invoices_ledger", table_name="invoices")
    op.drop_column("invoices", "monthly_rate_paise")
    op.drop_column("invoices", "covers_to")
    op.drop_column("invoices", "covers_from")
    op.drop_column("invoices", "period_months")
    op.drop_column("invoices", "ledger")
