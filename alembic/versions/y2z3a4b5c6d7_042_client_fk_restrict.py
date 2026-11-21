"""042 change client FKs from CASCADE to RESTRICT

Revision ID: y2z3a4b5c6d7
Revises: x1y2z3a4b5c6
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y2z3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "x1y2z3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _swap_fk(table: str, column: str, fk_name: str) -> None:
    """Drop CASCADE FK and recreate as RESTRICT (PostgreSQL)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return
    fks = inspector.get_foreign_keys(table)
    matching = [
        fk
        for fk in fks
        if fk.get("referred_table") == "clients" and column in (fk.get("constrained_columns") or [])
    ]
    for fk in matching:
        name = fk.get("name")
        if name:
            op.drop_constraint(name, table, type_="foreignkey")
    op.create_foreign_key(
        fk_name,
        table,
        "clients",
        [column],
        ["id"],
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    _swap_fk("projects", "client_id", "fk_projects_client_id_restrict")
    _swap_fk("invoices", "client_id", "fk_invoices_client_id_restrict")
    _swap_fk("recurring_invoices", "client_id", "fk_recurring_invoices_client_id_restrict")


def downgrade() -> None:
    # Restore CASCADE (previous behaviour)
    for table, column, new_name in [
        ("projects", "client_id", "fk_projects_client_id_cascade"),
        ("invoices", "client_id", "fk_invoices_client_id_cascade"),
        ("recurring_invoices", "client_id", "fk_recurring_invoices_client_id_cascade"),
    ]:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if table not in inspector.get_table_names():
            continue
        fks = inspector.get_foreign_keys(table)
        for fk in fks:
            if fk.get("referred_table") == "clients" and column in (fk.get("constrained_columns") or []):
                name = fk.get("name")
                if name:
                    op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            new_name, table, "clients", [column], ["id"], ondelete="CASCADE"
        )
