"""017_invoice_service_prefixes

Company code KRWS + per-service/year invoice sequences (KRWS-DEV-2026-001).

Revision ID: q3l4m5n6o7p8
Revises: p2k3l4m5n6o7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


revision: str = "q3l4m5n6o7p8"
down_revision: Union[str, Sequence[str], None] = "p2k3l4m5n6o7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "settings" not in tables:
        return
    cols = {c["name"]: c for c in inspector.get_columns("settings")}

    if "invoice_sequences" not in cols:
        op.add_column(
            "settings",
            sa.Column("invoice_sequences", JSON, nullable=False, server_default=sa.text("'{}'::json")),
        )

    # Widen prefix column if needed and set KRWS default for blank/legacy INV-* values
    op.execute(
        sa.text(
            """
            UPDATE settings
            SET invoice_prefix = 'KRWS'
            WHERE id = 'default'
              AND (
                invoice_prefix IS NULL
                OR invoice_prefix = ''
                OR invoice_prefix ILIKE 'INV%'
              )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "settings" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("settings")}
    if "invoice_sequences" in cols:
        op.drop_column("settings", "invoice_sequences")
