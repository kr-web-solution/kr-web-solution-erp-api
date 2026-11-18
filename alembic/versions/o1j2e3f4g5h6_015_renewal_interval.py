"""015_renewal_interval

Renewal cadence (months) on company expenses for recurring subscriptions.

Revision ID: o1j2e3f4g5h6
Revises: n0i1d2e3f4g5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "o1j2e3f4g5h6"
down_revision: Union[str, Sequence[str], None] = "n0i1d2e3f4g5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "company_expenses" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("company_expenses")}
    if "renewal_interval_months" not in cols:
        op.add_column(
            "company_expenses",
            sa.Column("renewal_interval_months", sa.Integer(), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE company_expenses SET renewal_interval_months = 1 "
                "WHERE section = 'recurring' AND renewal_interval_months IS NULL"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "company_expenses" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("company_expenses")}
    if "renewal_interval_months" in cols:
        op.drop_column("company_expenses", "renewal_interval_months")
