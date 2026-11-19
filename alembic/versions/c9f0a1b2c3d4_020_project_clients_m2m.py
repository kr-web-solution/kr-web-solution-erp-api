"""020_project_clients_m2m

Many clients per project; project must keep at least one client.
Backfills from existing projects.client_id.

Revision ID: c9f0a1b2c3d4
Revises: b8e1c2d3f4a5
Create Date: 2026-11-19 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9f0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "b8e1c2d3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_clients",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "client_id"),
    )
    op.create_index("ix_project_clients_client_id", "project_clients", ["client_id"], unique=False)
    op.create_index("ix_project_clients_project_id", "project_clients", ["project_id"], unique=False)

    # Backfill: every existing project ↔ its primary client_id
    op.execute(
        sa.text(
            """
            INSERT INTO project_clients (project_id, client_id)
            SELECT id, client_id
            FROM projects
            WHERE client_id IS NOT NULL
              AND deleted_at IS NULL
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_project_clients_project_id", table_name="project_clients")
    op.drop_index("ix_project_clients_client_id", table_name="project_clients")
    op.drop_table("project_clients")
