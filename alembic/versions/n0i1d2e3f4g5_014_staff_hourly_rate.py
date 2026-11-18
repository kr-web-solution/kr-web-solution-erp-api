"""014_staff_hourly_rate

Per-hour salary on staff user profiles.

Revision ID: n0i1d2e3f4g5
Revises: m9h0c1d2e3f4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "n0i1d2e3f4g5"
down_revision: Union[str, Sequence[str], None] = "m9h0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "hourly_rate_paise" not in cols:
        op.add_column(
            "users",
            sa.Column("hourly_rate_paise", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "hourly_rate_paise" in cols:
        op.drop_column("users", "hourly_rate_paise")
