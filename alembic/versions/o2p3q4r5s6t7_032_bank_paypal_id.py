"""032 add bank_paypal_id to company settings

Revision ID: o2p3q4r5s6t7
Revises: n1o2p3q4r5s6
Create Date: 2026-03-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o2p3q4r5s6t7"
down_revision: Union[str, Sequence[str], None] = "n1o2p3q4r5s6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "settings" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("settings")}
    if "bank_paypal_id" not in cols:
        op.add_column(
            "settings",
            sa.Column("bank_paypal_id", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "settings" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("settings")}
    if "bank_paypal_id" in cols:
        op.drop_column("settings", "bank_paypal_id")
