"""016_settings_bank_fields

Split company bank / UPI payment details into separate columns.

Revision ID: p2k3l4m5n6o7
Revises: o1j2e3f4g5h6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "p2k3l4m5n6o7"
down_revision: Union[str, Sequence[str], None] = "o1j2e3f4g5h6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_COLS = (
    ("bank_beneficiary_name", sa.String(length=255)),
    ("bank_name", sa.String(length=255)),
    ("bank_account_number", sa.String(length=64)),
    ("bank_ifsc", sa.String(length=32)),
    ("bank_upi_id", sa.String(length=128)),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "settings" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("settings")}
    for name, col_type in NEW_COLS:
        if name not in cols:
            op.add_column("settings", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "settings" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("settings")}
    for name, _ in reversed(NEW_COLS):
        if name in cols:
            op.drop_column("settings", name)
