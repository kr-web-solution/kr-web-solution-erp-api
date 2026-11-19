"""022_project_clients_is_primary

- Add project_clients.is_primary (bool)
- Backfill primary from projects.client_id
- Ensure every active project has ≥1 project_clients row
- Partial unique index: one primary per project

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-11-19 14:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "project_clients",
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    # Link any active projects missing membership (from projects.client_id)
    op.execute(
        sa.text(
            """
            INSERT INTO project_clients (project_id, client_id, is_primary)
            SELECT p.id, p.client_id, true
            FROM projects p
            WHERE p.deleted_at IS NULL
              AND p.client_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM project_clients pc WHERE pc.project_id = p.id
              )
            ON CONFLICT DO NOTHING
            """
        )
    )

    # Mark primary from projects.client_id where the link exists
    op.execute(
        sa.text(
            """
            UPDATE project_clients pc
            SET is_primary = true
            FROM projects p
            WHERE pc.project_id = p.id
              AND pc.client_id = p.client_id
            """
        )
    )

    # If a project still has no primary, promote the lowest client_id
    op.execute(
        sa.text(
            """
            UPDATE project_clients pc
            SET is_primary = true
            WHERE pc.ctid IN (
              SELECT DISTINCT ON (pc2.project_id) pc2.ctid
              FROM project_clients pc2
              WHERE NOT EXISTS (
                SELECT 1 FROM project_clients x
                WHERE x.project_id = pc2.project_id AND x.is_primary = true
              )
              ORDER BY pc2.project_id, pc2.client_id
            )
            """
        )
    )

    # Sync projects.client_id from the primary link (service will keep this in sync)
    op.execute(
        sa.text(
            """
            UPDATE projects p
            SET client_id = pc.client_id
            FROM project_clients pc
            WHERE pc.project_id = p.id
              AND pc.is_primary = true
              AND (p.client_id IS DISTINCT FROM pc.client_id)
            """
        )
    )

    op.create_index(
        "uq_project_clients_one_primary",
        "project_clients",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_primary = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_project_clients_one_primary", table_name="project_clients")
    op.drop_column("project_clients", "is_primary")
