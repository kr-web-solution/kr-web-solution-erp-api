"""029 bank_wire_instructions on settings

Revision ID: l9m0n1o2p3q4
Revises: k8l9m0n1o2p3
Create Date: 2026-03-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l9m0n1o2p3q4"
down_revision: Union[str, Sequence[str], None] = "k8l9m0n1o2p3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("settings")}
    if "bank_wire_instructions" not in cols:
        op.add_column(
            "settings",
            sa.Column("bank_wire_instructions", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("settings")}
    if "bank_wire_instructions" in cols:
        op.drop_column("settings", "bank_wire_instructions")
