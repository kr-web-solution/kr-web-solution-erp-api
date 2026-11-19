"""024_invoice_item_line_kind

Per-line service kind so one invoice can mix development + maintenance (+ etc).

Revision ID: g4h5i6j7k8l9
Revises: f3a4b5c6d7e8
Create Date: 2026-11-19 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g4h5i6j7k8l9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invoice_items",
        sa.Column("line_kind", sa.String(length=40), nullable=True),
    )
    op.create_index("ix_invoice_items_line_kind", "invoice_items", ["line_kind"])


def downgrade() -> None:
    op.drop_index("ix_invoice_items_line_kind", table_name="invoice_items")
    op.drop_column("invoice_items", "line_kind")
