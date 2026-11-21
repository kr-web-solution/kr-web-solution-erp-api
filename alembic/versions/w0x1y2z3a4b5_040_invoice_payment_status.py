"""040 invoice payment status + AR view fix

Revision ID: w0x1y2z3a4b5
Revises: v9w0x1y2z3a4
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w0x1y2z3a4b5"
down_revision: Union[str, Sequence[str], None] = "v9w0x1y2z3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "invoice_payments" in tables:
        cols = {c["name"] for c in inspector.get_columns("invoice_payments")}
        if "status" not in cols:
            op.add_column(
                "invoice_payments",
                sa.Column(
                    "status",
                    sa.String(length=20),
                    nullable=False,
                    server_default="verified",
                ),
            )
            op.create_index("ix_invoice_payments_status", "invoice_payments", ["status"])
        for name, col in [
            ("rejection_reason", sa.Column("rejection_reason", sa.Text(), nullable=True)),
            ("verified_by", sa.Column("verified_by", sa.String(length=36), nullable=True)),
            ("verified_at", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True)),
            ("rejected_by", sa.Column("rejected_by", sa.String(length=36), nullable=True)),
            ("rejected_at", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True)),
            ("proof_object_key", sa.Column("proof_object_key", sa.String(length=512), nullable=True)),
            ("proof_filename", sa.Column("proof_filename", sa.String(length=255), nullable=True)),
            (
                "proof_content_type",
                sa.Column("proof_content_type", sa.String(length=120), nullable=True),
            ),
        ]:
            if name not in cols:
                op.add_column("invoice_payments", col)

        # Backfill existing rows as verified
        op.execute(
            sa.text(
                "UPDATE invoice_payments SET status = 'verified' "
                "WHERE status IS NULL OR status = ''"
            )
        )

        # Partial unique on (invoice_id, lower(reference)) for non-rejected active rows
        op.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_payments_invoice_ref_active
                ON invoice_payments (invoice_id, lower(reference))
                WHERE reference IS NOT NULL
                  AND btrim(reference) <> ''
                  AND status <> 'rejected'
                  AND deleted_at IS NULL
                """
            )
        )

    # AR views: outstanding uses total - amount_paid for open invoices
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE VIEW v_client_ar_summary AS
            SELECT
              c.id AS client_id,
              c.name AS client_name,
              COUNT(i.id) FILTER (
                WHERE i.status IN ('sent', 'overdue', 'proof_pending')
              )::bigint AS open_invoice_count,
              COALESCE(SUM(
                GREATEST(i.total_paise - COALESCE(i.amount_paid_paise, 0), 0)
              ) FILTER (
                WHERE i.status IN ('sent', 'overdue', 'proof_pending')
              ), 0)::bigint AS outstanding_paise,
              COALESCE(SUM(i.amount_paid_paise) FILTER (
                WHERE i.status NOT IN ('draft', 'cancelled', 'void')
              ), 0)::bigint AS paid_paise,
              COALESCE(SUM(i.total_paise) FILTER (
                WHERE i.status NOT IN ('draft', 'cancelled', 'void')
              ), 0)::bigint AS billed_paise
            FROM clients c
            LEFT JOIN invoices i ON i.client_id = c.id AND i.deleted_at IS NULL
            WHERE c.deleted_at IS NULL
            GROUP BY c.id, c.name
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE VIEW v_dashboard_founder_kpis AS
            SELECT
              (SELECT COUNT(*) FROM projects WHERE deleted_at IS NULL AND status = 'running')::bigint AS running_projects,
              (SELECT COUNT(*) FROM leads WHERE deleted_at IS NULL AND status IN ('new', 'contacted', 'proposal'))::bigint AS open_leads,
              (SELECT COALESCE(SUM(GREATEST(total_paise - COALESCE(amount_paid_paise, 0), 0)), 0)
                 FROM invoices
                 WHERE deleted_at IS NULL AND status IN ('sent', 'overdue', 'proof_pending'))::bigint AS outstanding_paise,
              (SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL AND status = 'overdue')::bigint AS overdue_invoices,
              (SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL AND email_status = 'failed')::bigint AS failed_email_invoices,
              (SELECT COUNT(*) FROM late_fee_actions WHERE deleted_at IS NULL AND status = 'pending')::bigint AS pending_late_fees,
              (SELECT COUNT(*) FROM engagements WHERE deleted_at IS NULL AND status = 'active')::bigint AS active_engagements,
              (SELECT COALESCE(SUM(amount_paise), 0) FROM company_expenses
                 WHERE deleted_at IS NULL AND expense_date >= (date_trunc('month', CURRENT_DATE))::date)::bigint AS spend_this_month_paise
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_invoice_payments_invoice_ref_active"))
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "invoice_payments" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("invoice_payments")}
        for name in [
            "proof_content_type",
            "proof_filename",
            "proof_object_key",
            "rejected_at",
            "rejected_by",
            "verified_at",
            "verified_by",
            "rejection_reason",
            "status",
        ]:
            if name in cols:
                op.drop_column("invoice_payments", name)
