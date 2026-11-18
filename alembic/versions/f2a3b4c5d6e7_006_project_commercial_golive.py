"""006_project_commercial_golive

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-06 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("development_days", sa.Integer(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("advance_paise", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("projects", sa.Column("advance_invoice_id", sa.String(length=36), nullable=True))
    op.add_column("projects", sa.Column("balance_invoice_id", sa.String(length=36), nullable=True))
    op.add_column("projects", sa.Column("go_live_at", sa.Date(), nullable=True))
    op.create_foreign_key(
        "fk_projects_advance_invoice_id",
        "projects",
        "invoices",
        ["advance_invoice_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_projects_balance_invoice_id",
        "projects",
        "invoices",
        ["balance_invoice_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_projects_balance_invoice_id", "projects", type_="foreignkey")
    op.drop_constraint("fk_projects_advance_invoice_id", "projects", type_="foreignkey")
    op.drop_column("projects", "go_live_at")
    op.drop_column("projects", "balance_invoice_id")
    op.drop_column("projects", "advance_invoice_id")
    op.drop_column("projects", "advance_paise")
    op.drop_column("projects", "development_days")
