"""030 invoice document_snapshot freeze

Revision ID: m0n1o2p3q4r5
Revises: l9m0n1o2p3q4
Create Date: 2026-03-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m0n1o2p3q4r5"
down_revision: Union[str, Sequence[str], None] = "l9m0n1o2p3q4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("invoices")}
    if "document_snapshot" not in cols:
        op.add_column(
            "invoices",
            sa.Column(
                "document_snapshot",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )
    if "snapshot_frozen_at" not in cols:
        op.add_column(
            "invoices",
            sa.Column("snapshot_frozen_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("invoices")}
    if "snapshot_frozen_at" in cols:
        op.drop_column("invoices", "snapshot_frozen_at")
    if "document_snapshot" in cols:
        op.drop_column("invoices", "document_snapshot")
