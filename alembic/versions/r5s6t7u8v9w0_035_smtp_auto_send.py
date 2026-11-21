"""035 smtp_auto_send_enabled for automatic invoice emails

Revision ID: r5s6t7u8v9w0
Revises: q4r5s6t7u8v9
Create Date: 2026-11-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r5s6t7u8v9w0"
down_revision: Union[str, Sequence[str], None] = "q4r5s6t7u8v9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "settings" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("settings")}
    if "smtp_auto_send_enabled" not in cols:
        op.add_column(
            "settings",
            sa.Column(
                "smtp_auto_send_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "settings" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("settings")}
    if "smtp_auto_send_enabled" in cols:
        op.drop_column("settings", "smtp_auto_send_enabled")
