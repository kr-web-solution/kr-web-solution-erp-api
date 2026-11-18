"""018_gst_payroll_upi_qr

Optional GST / PF / ESI / PT flags (default off), tax default 0%,
company UPI QR URL, per-invoice UPI overrides.

Revision ID: r4m5n6o7p8q9
Revises: q3l4m5n6o7p8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r4m5n6o7p8q9"
down_revision: Union[str, Sequence[str], None] = "q3l4m5n6o7p8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "settings" in tables:
        cols = {c["name"] for c in inspector.get_columns("settings")}
        adds = [
            ("gst_enabled", sa.Column("gst_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("pf_enabled", sa.Column("pf_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("pf_employee_percent", sa.Column("pf_employee_percent", sa.Numeric(5, 2), nullable=False, server_default="0")),
            ("esi_enabled", sa.Column("esi_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("esi_employee_percent", sa.Column("esi_employee_percent", sa.Numeric(5, 2), nullable=False, server_default="0")),
            ("pt_enabled", sa.Column("pt_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("pt_paise", sa.Column("pt_paise", sa.BigInteger(), nullable=False, server_default="0")),
            ("bank_upi_qr_url", sa.Column("bank_upi_qr_url", sa.String(length=500), nullable=True)),
        ]
        for name, col in adds:
            if name not in cols:
                op.add_column("settings", col)

        op.execute(
            sa.text(
                """
                UPDATE settings
                SET default_tax_rate = 0,
                    gst_enabled = false,
                    pf_enabled = false,
                    esi_enabled = false,
                    pt_enabled = false
                WHERE id = 'default'
                """
            )
        )

    if "invoices" in tables:
        inv_cols = {c["name"] for c in inspector.get_columns("invoices")}
        if "upi_id_override" not in inv_cols:
            op.add_column("invoices", sa.Column("upi_id_override", sa.String(length=128), nullable=True))
        if "upi_qr_url" not in inv_cols:
            op.add_column("invoices", sa.Column("upi_qr_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "invoices" in tables:
        inv_cols = {c["name"] for c in inspector.get_columns("invoices")}
        for name in ("upi_qr_url", "upi_id_override"):
            if name in inv_cols:
                op.drop_column("invoices", name)

    if "settings" in tables:
        cols = {c["name"] for c in inspector.get_columns("settings")}
        for name in (
            "bank_upi_qr_url",
            "pt_paise",
            "pt_enabled",
            "esi_employee_percent",
            "esi_enabled",
            "pf_employee_percent",
            "pf_enabled",
            "gst_enabled",
        ):
            if name in cols:
                op.drop_column("settings", name)
