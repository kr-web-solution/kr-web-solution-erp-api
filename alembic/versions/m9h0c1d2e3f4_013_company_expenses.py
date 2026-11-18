"""013_company_expenses

Company spend ledger for studio costs (recurring, daily, upgrades, other).

Revision ID: m9h0c1d2e3f4
Revises: l8g9b0c1d2e3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "m9h0c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = "l8g9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "company_expenses" not in tables:
        op.create_table(
            "company_expenses",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("expense_date", sa.Date(), nullable=False),
            sa.Column("section", sa.String(length=20), nullable=False),
            sa.Column("category", sa.String(length=40), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("vendor", sa.String(length=255), nullable=True),
            sa.Column("amount_paise", sa.BigInteger(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("receipt_object_key", sa.String(length=500), nullable=True),
            sa.Column("receipt_original_filename", sa.String(length=255), nullable=True),
            sa.Column("receipt_content_type", sa.String(length=120), nullable=True),
            sa.Column("next_renewal_on", sa.Date(), nullable=True),
            sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_company_expenses_id", "company_expenses", ["id"])
        op.create_index("ix_company_expenses_expense_date", "company_expenses", ["expense_date"])
        op.create_index("ix_company_expenses_section", "company_expenses", ["section"])
        op.create_index("ix_company_expenses_category", "company_expenses", ["category"])
        op.create_index("ix_company_expenses_next_renewal_on", "company_expenses", ["next_renewal_on"])
        op.create_index("ix_company_expenses_created_by_user_id", "company_expenses", ["created_by_user_id"])


def downgrade() -> None:
    op.drop_table("company_expenses")
