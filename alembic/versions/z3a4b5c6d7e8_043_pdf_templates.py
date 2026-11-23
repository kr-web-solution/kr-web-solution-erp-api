"""043 pdf_templates registry

Revision ID: z3a4b5c6d7e8
Revises: y2z3a4b5c6d7
Create Date: 2026-11-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "y2z3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "pdf_templates" not in tables:
        op.create_table(
            "pdf_templates",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("doc_type", sa.String(length=40), nullable=False),
            sa.Column("design_key", sa.String(length=80), nullable=False),
            sa.Column("version", sa.String(length=20), nullable=False),
            sa.Column("label", sa.String(length=120), nullable=False),
            sa.Column(
                "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
            ),
            sa.Column(
                "is_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "doc_type",
                "design_key",
                "version",
                name="uq_pdf_templates_doc_design_ver",
            ),
        )

    # Refresh inspector after possible create
    inspector = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("pdf_templates")}
    if "ix_pdf_templates_doc_active" not in existing_indexes:
        op.create_index(
            "ix_pdf_templates_doc_active", "pdf_templates", ["doc_type", "active"]
        )
    if "ix_pdf_templates_id" not in existing_indexes:
        op.create_index("ix_pdf_templates_id", "pdf_templates", ["id"])
    if "ix_pdf_templates_doc_type" not in existing_indexes:
        op.create_index("ix_pdf_templates_doc_type", "pdf_templates", ["doc_type"])

    # Ensure unique constraint exists when table came from create_all
    uqs = {
        uq.get("name")
        for uq in inspector.get_unique_constraints("pdf_templates")
    }
    if "uq_pdf_templates_doc_design_ver" not in uqs:
        op.create_unique_constraint(
            "uq_pdf_templates_doc_design_ver",
            "pdf_templates",
            ["doc_type", "design_key", "version"],
        )

    # Seed default filesystem designs
    op.execute(
        """
        INSERT INTO pdf_templates (id, doc_type, design_key, version, label, active, is_default)
        VALUES
          ('pdt_invoice_v1', 'invoice', 'invoices/v1', 'v1', 'Invoice Classic', true, true),
          ('pdt_receipt_v1', 'receipt', 'invoices/v1', 'v1', 'Receipt (paid invoice)', true, true),
          ('pdt_payslip_v1', 'payslip', 'payslips/v1', 'v1', 'Payslip Classic', true, true),
          ('pdt_quote_v1', 'quotation', 'quotations/v1', 'v1', 'Quotation Classic', true, true),
          ('pdt_po_v1', 'purchase_order', 'purchase_orders/v1', 'v1', 'Purchase Order Classic', true, true),
          ('pdt_report_v1', 'report', 'reports/v1', 'v1', 'Financial Report Classic', true, true)
        ON CONFLICT ON CONSTRAINT uq_pdf_templates_doc_design_ver DO NOTHING
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "pdf_templates" not in inspector.get_table_names():
        return
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("pdf_templates")}
    for name in (
        "ix_pdf_templates_doc_type",
        "ix_pdf_templates_id",
        "ix_pdf_templates_doc_active",
    ):
        if name in existing_indexes:
            op.drop_index(name, table_name="pdf_templates")
    op.drop_table("pdf_templates")
