"""028_invoice_engine_fields

Additive invoice engine columns: GST split snapshots, commercial fields,
company/client tax identity, terms-by-type JSON, and invoice_payments table.

Revision ID: k8l9m0n1o2p3
Revises: j7k8l9m0n1o2
Create Date: 2026-11-20 08:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "k8l9m0n1o2p3"
down_revision: Union[str, Sequence[str], None] = "j7k8l9m0n1o2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in inspect(bind).get_columns(table)}


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(table)


def _has_index(table: str, name: str) -> bool:
    bind = op.get_bind()
    return any(ix["name"] == name for ix in inspect(bind).get_indexes(table))


def upgrade() -> None:
    # —— settings ——
    sc = _cols("settings")
    if "company_pan" not in sc:
        op.add_column("settings", sa.Column("company_pan", sa.String(length=20), nullable=True))
    if "company_state" not in sc:
        op.add_column("settings", sa.Column("company_state", sa.String(length=120), nullable=True))
    if "company_state_code" not in sc:
        op.add_column("settings", sa.Column("company_state_code", sa.String(length=4), nullable=True))
    if "gst_unregistered" not in sc:
        op.add_column(
            "settings",
            sa.Column("gst_unregistered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if "invoice_terms_by_type" not in sc:
        op.add_column(
            "settings",
            sa.Column("invoice_terms_by_type", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        )
    if "default_sac_code" not in sc:
        op.add_column("settings", sa.Column("default_sac_code", sa.String(length=20), nullable=True))

    # —— clients ——
    cc = _cols("clients")
    if "pan" not in cc:
        op.add_column("clients", sa.Column("pan", sa.String(length=20), nullable=True))
    if "state_code" not in cc:
        op.add_column("clients", sa.Column("state_code", sa.String(length=4), nullable=True))

    # —— invoices ——
    ic = _cols("invoices")
    for name, col in [
        ("place_of_supply", sa.Column("place_of_supply", sa.String(length=120), nullable=True)),
        ("supply_type", sa.Column("supply_type", sa.String(length=16), nullable=True)),
        ("cgst_paise", sa.Column("cgst_paise", sa.BigInteger(), nullable=False, server_default="0")),
        ("sgst_paise", sa.Column("sgst_paise", sa.BigInteger(), nullable=False, server_default="0")),
        ("igst_paise", sa.Column("igst_paise", sa.BigInteger(), nullable=False, server_default="0")),
        ("discount_paise", sa.Column("discount_paise", sa.BigInteger(), nullable=False, server_default="0")),
        ("round_off_paise", sa.Column("round_off_paise", sa.BigInteger(), nullable=False, server_default="0")),
        ("amount_paid_paise", sa.Column("amount_paid_paise", sa.BigInteger(), nullable=False, server_default="0")),
        ("po_number", sa.Column("po_number", sa.String(length=100), nullable=True)),
    ]:
        if name not in ic:
            op.add_column("invoices", col)

    # —— invoice_items ——
    iic = _cols("invoice_items")
    if "hsn_sac" not in iic:
        op.add_column("invoice_items", sa.Column("hsn_sac", sa.String(length=20), nullable=True))
    if "unit" not in iic:
        op.add_column("invoice_items", sa.Column("unit", sa.String(length=32), nullable=True))

    # —— invoice_payments ——
    if not _has_table("invoice_payments"):
        op.create_table(
            "invoice_payments",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "invoice_id",
                sa.String(length=36),
                sa.ForeignKey("invoices.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("amount_paise", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("method", sa.String(length=40), nullable=True),
            sa.Column("reference", sa.String(length=120), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column(
                "recorded_by",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
    if _has_table("invoice_payments"):
        if not _has_index("invoice_payments", "ix_invoice_payments_invoice_id"):
            op.create_index("ix_invoice_payments_invoice_id", "invoice_payments", ["invoice_id"])
        if not _has_index("invoice_payments", "ix_invoice_payments_invoice_paid_at"):
            op.create_index(
                "ix_invoice_payments_invoice_paid_at",
                "invoice_payments",
                ["invoice_id", "paid_at"],
            )


def downgrade() -> None:
    if _has_table("invoice_payments"):
        if _has_index("invoice_payments", "ix_invoice_payments_invoice_paid_at"):
            op.drop_index("ix_invoice_payments_invoice_paid_at", table_name="invoice_payments")
        if _has_index("invoice_payments", "ix_invoice_payments_invoice_id"):
            op.drop_index("ix_invoice_payments_invoice_id", table_name="invoice_payments")
        op.drop_table("invoice_payments")

    iic = _cols("invoice_items")
    if "unit" in iic:
        op.drop_column("invoice_items", "unit")
    if "hsn_sac" in iic:
        op.drop_column("invoice_items", "hsn_sac")

    ic = _cols("invoices")
    for name in (
        "po_number",
        "amount_paid_paise",
        "round_off_paise",
        "discount_paise",
        "igst_paise",
        "sgst_paise",
        "cgst_paise",
        "supply_type",
        "place_of_supply",
    ):
        if name in ic:
            op.drop_column("invoices", name)

    cc = _cols("clients")
    if "state_code" in cc:
        op.drop_column("clients", "state_code")
    if "pan" in cc:
        op.drop_column("clients", "pan")

    sc = _cols("settings")
    for name in (
        "default_sac_code",
        "invoice_terms_by_type",
        "gst_unregistered",
        "company_state_code",
        "company_state",
        "company_pan",
    ):
        if name in sc:
            op.drop_column("settings", name)
