"""033 per-invoice gst_enabled override (null = follow settings)

Revision ID: p3q4r5s6t7u8
Revises: o2p3q4r5s6t7
Create Date: 2026-03-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p3q4r5s6t7u8"
down_revision: Union[str, Sequence[str], None] = "o2p3q4r5s6t7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "invoices" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("invoices")}
    if "gst_enabled" not in cols:
        op.add_column(
            "invoices",
            sa.Column("gst_enabled", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "invoices" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("invoices")}
    if "gst_enabled" in cols:
        op.drop_column("invoices", "gst_enabled")
