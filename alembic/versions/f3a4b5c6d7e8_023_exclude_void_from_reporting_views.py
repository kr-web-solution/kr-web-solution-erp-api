"""023_exclude_void_from_reporting_views

F-043: billed aggregates must exclude cancelled/void (and already exclude soft-deleted).

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-11-19 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW v_invoice_monthly_billed AS
        SELECT
          to_char(issue_date, 'YYYY-MM') AS month_key,
          (date_trunc('month', issue_date::timestamp))::date AS month_start,
          ledger,
          COUNT(*)::bigint AS invoice_count,
          COALESCE(SUM(total_paise), 0)::bigint AS billed_paise
        FROM invoices
        WHERE deleted_at IS NULL
          AND status NOT IN ('draft', 'cancelled', 'void')
        GROUP BY 1, 2, 3
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v_client_ar_summary AS
        SELECT
          c.id AS client_id,
          c.name AS client_name,
          COUNT(i.id) FILTER (WHERE i.status IN ('sent', 'overdue'))::bigint AS open_invoice_count,
          COALESCE(SUM(i.total_paise) FILTER (WHERE i.status IN ('sent', 'overdue')), 0)::bigint AS outstanding_paise,
          COALESCE(SUM(i.total_paise) FILTER (WHERE i.status = 'paid'), 0)::bigint AS paid_paise,
          COALESCE(SUM(i.total_paise) FILTER (
            WHERE i.status NOT IN ('draft', 'cancelled', 'void')
          ), 0)::bigint AS billed_paise
        FROM clients c
        LEFT JOIN invoices i ON i.client_id = c.id AND i.deleted_at IS NULL
        WHERE c.deleted_at IS NULL
        GROUP BY c.id, c.name
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW v_invoice_monthly_billed AS
        SELECT
          to_char(issue_date, 'YYYY-MM') AS month_key,
          (date_trunc('month', issue_date::timestamp))::date AS month_start,
          ledger,
          COUNT(*)::bigint AS invoice_count,
          COALESCE(SUM(total_paise), 0)::bigint AS billed_paise
        FROM invoices
        WHERE deleted_at IS NULL AND status <> 'draft'
        GROUP BY 1, 2, 3
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v_client_ar_summary AS
        SELECT
          c.id AS client_id,
          c.name AS client_name,
          COUNT(i.id) FILTER (WHERE i.status IN ('sent', 'overdue'))::bigint AS open_invoice_count,
          COALESCE(SUM(i.total_paise) FILTER (WHERE i.status IN ('sent', 'overdue')), 0)::bigint AS outstanding_paise,
          COALESCE(SUM(i.total_paise) FILTER (WHERE i.status = 'paid'), 0)::bigint AS paid_paise,
          COALESCE(SUM(i.total_paise) FILTER (WHERE i.status <> 'draft'), 0)::bigint AS billed_paise
        FROM clients c
        LEFT JOIN invoices i ON i.client_id = c.id AND i.deleted_at IS NULL
        WHERE c.deleted_at IS NULL
        GROUP BY c.id, c.name
        """
    )
