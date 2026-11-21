"""031 gst_registrations + document logo content hash support

Revision ID: n1o2p3q4r5s6
Revises: m0n1o2p3q4r5
Create Date: 2026-03-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n1o2p3q4r5s6"
down_revision: Union[str, Sequence[str], None] = "m0n1o2p3q4r5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "gst_registrations" not in tables:
        op.create_table(
            "gst_registrations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("gstin", sa.String(length=20), nullable=False),
            sa.Column("legal_name", sa.String(length=255), nullable=True),
            sa.Column("address", sa.Text(), nullable=True),
            sa.Column("state", sa.String(length=120), nullable=True),
            sa.Column("state_code", sa.String(length=4), nullable=False),
            sa.Column("effective_from", sa.Date(), nullable=False),
            sa.Column("effective_to", sa.Date(), nullable=True),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_gst_registrations_state_code", "gst_registrations", ["state_code"])
        op.create_index("ix_gst_registrations_effective", "gst_registrations", ["effective_from", "effective_to"])
        op.create_index("ix_gst_registrations_gstin", "gst_registrations", ["gstin"])

    # Optional content-hash column on settings for immutable logo reference
    cols = {c["name"] for c in inspector.get_columns("settings")}
    if "company_logo_content_hash" not in cols:
        op.add_column(
            "settings",
            sa.Column("company_logo_content_hash", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("settings")}
    if "company_logo_content_hash" in cols:
        op.drop_column("settings", "company_logo_content_hash")
    tables = set(inspector.get_table_names())
    if "gst_registrations" in tables:
        op.drop_index("ix_gst_registrations_gstin", table_name="gst_registrations")
        op.drop_index("ix_gst_registrations_effective", table_name="gst_registrations")
        op.drop_index("ix_gst_registrations_state_code", table_name="gst_registrations")
        op.drop_table("gst_registrations")
