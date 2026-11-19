"""021_project_invoice_plan_rules

- project_clients.client_id ondelete RESTRICT
- invoices.project_id NOT NULL + RESTRICT (backfill first)
- recurring_invoices.project_id NOT NULL + RESTRICT (backfill first)

Revision ID: d1e2f3a4b5c6
Revises: c9f0a1b2c3d4
Create Date: 2026-11-19 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_fks_on_column(table: str, column: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys(table):
        if column in (fk.get("constrained_columns") or []):
            op.drop_constraint(fk["name"], table, type_="foreignkey")


def upgrade() -> None:
    # --- project_clients: client_id CASCADE → RESTRICT ---
    _drop_fks_on_column("project_clients", "client_id")
    op.create_foreign_key(
        "project_clients_client_id_fkey",
        "project_clients",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # --- Backfill invoices.project_id ---
    op.execute(
        sa.text(
            """
            UPDATE invoices i
            SET project_id = sub.project_id
            FROM (
                SELECT DISTINCT ON (pc.client_id) pc.client_id, pc.project_id
                FROM project_clients pc
                JOIN projects p ON p.id = pc.project_id AND p.deleted_at IS NULL
                ORDER BY pc.client_id, pc.project_id
            ) AS sub
            WHERE i.project_id IS NULL
              AND i.client_id = sub.client_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE invoices i
            SET project_id = p.id
            FROM projects p
            WHERE i.project_id IS NULL
              AND p.client_id = i.client_id
              AND p.deleted_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO projects (id, name, client_id, budget_paise, status, delivery_phase, project_type, created_at, updated_at)
            SELECT
                'prj_orphan_' || substr(md5(c.id), 1, 8),
                'Archived (orphan invoices) — ' || left(c.name, 180),
                c.id,
                0,
                'stopped',
                5,
                'custom',
                now(),
                now()
            FROM clients c
            WHERE EXISTS (
                SELECT 1 FROM invoices i WHERE i.client_id = c.id AND i.project_id IS NULL
            )
            AND NOT EXISTS (
                SELECT 1 FROM projects p WHERE p.id = 'prj_orphan_' || substr(md5(c.id), 1, 8)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO project_clients (project_id, client_id)
            SELECT p.id, p.client_id
            FROM projects p
            WHERE p.id LIKE 'prj_orphan_%'
            ON CONFLICT DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE invoices i
            SET project_id = 'prj_orphan_' || substr(md5(i.client_id), 1, 8)
            WHERE i.project_id IS NULL
            """
        )
    )

    _drop_fks_on_column("invoices", "project_id")
    op.alter_column("invoices", "project_id", existing_type=sa.String(length=36), nullable=False)
    op.create_foreign_key(
        "invoices_project_id_fkey",
        "invoices",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # --- Backfill recurring_invoices.project_id ---
    op.execute(
        sa.text(
            """
            UPDATE recurring_invoices r
            SET project_id = sub.project_id
            FROM (
                SELECT DISTINCT ON (pc.client_id) pc.client_id, pc.project_id
                FROM project_clients pc
                JOIN projects p ON p.id = pc.project_id AND p.deleted_at IS NULL
                ORDER BY pc.client_id, pc.project_id
            ) AS sub
            WHERE r.project_id IS NULL
              AND r.client_id = sub.client_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE recurring_invoices r
            SET project_id = p.id
            FROM projects p
            WHERE r.project_id IS NULL
              AND p.client_id = r.client_id
              AND p.deleted_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO projects (id, name, client_id, budget_paise, status, delivery_phase, project_type, created_at, updated_at)
            SELECT
                'prj_orphplan_' || substr(md5(c.id), 1, 8),
                'Archived (orphan plans) — ' || left(c.name, 180),
                c.id,
                0,
                'stopped',
                5,
                'custom',
                now(),
                now()
            FROM clients c
            WHERE EXISTS (
                SELECT 1 FROM recurring_invoices r WHERE r.client_id = c.id AND r.project_id IS NULL
            )
            AND NOT EXISTS (
                SELECT 1 FROM projects p WHERE p.id = 'prj_orphplan_' || substr(md5(c.id), 1, 8)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO project_clients (project_id, client_id)
            SELECT p.id, p.client_id
            FROM projects p
            WHERE p.id LIKE 'prj_orphplan_%'
            ON CONFLICT DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE recurring_invoices r
            SET project_id = 'prj_orphplan_' || substr(md5(r.client_id), 1, 8)
            WHERE r.project_id IS NULL
            """
        )
    )

    _drop_fks_on_column("recurring_invoices", "project_id")
    op.alter_column(
        "recurring_invoices", "project_id", existing_type=sa.String(length=36), nullable=False
    )
    op.create_foreign_key(
        "recurring_invoices_project_id_fkey",
        "recurring_invoices",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    _drop_fks_on_column("recurring_invoices", "project_id")
    op.alter_column(
        "recurring_invoices", "project_id", existing_type=sa.String(length=36), nullable=True
    )
    op.create_foreign_key(
        "recurring_invoices_project_id_fkey",
        "recurring_invoices",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )

    _drop_fks_on_column("invoices", "project_id")
    op.alter_column("invoices", "project_id", existing_type=sa.String(length=36), nullable=True)
    op.create_foreign_key(
        "invoices_project_id_fkey",
        "invoices",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )

    _drop_fks_on_column("project_clients", "client_id")
    op.create_foreign_key(
        "project_clients_client_id_fkey",
        "project_clients",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
