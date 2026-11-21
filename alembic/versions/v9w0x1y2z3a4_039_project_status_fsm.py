"""039 project status FSM fields + status events

Revision ID: v9w0x1y2z3a4
Revises: u8v9w0x1y2z3
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v9w0x1y2z3a4"
down_revision: Union[str, Sequence[str], None] = "u8v9w0x1y2z3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "projects" in tables:
        cols = {c["name"] for c in inspector.get_columns("projects")}
        additions = [
            ("status_reason", sa.Column("status_reason", sa.Text(), nullable=True)),
            (
                "reopened_at",
                sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
            ),
            (
                "reopened_by",
                sa.Column("reopened_by", sa.String(length=36), nullable=True),
            ),
            (
                "development_completed_at",
                sa.Column("development_completed_at", sa.DateTime(timezone=True), nullable=True),
            ),
            (
                "development_completed_by",
                sa.Column("development_completed_by", sa.String(length=36), nullable=True),
            ),
            (
                "client_accepted_at",
                sa.Column("client_accepted_at", sa.DateTime(timezone=True), nullable=True),
            ),
            (
                "client_accepted_by",
                sa.Column("client_accepted_by", sa.String(length=36), nullable=True),
            ),
            (
                "project_closed_at",
                sa.Column("project_closed_at", sa.DateTime(timezone=True), nullable=True),
            ),
            (
                "project_closed_by",
                sa.Column("project_closed_by", sa.String(length=36), nullable=True),
            ),
            ("closed_reason", sa.Column("closed_reason", sa.Text(), nullable=True)),
        ]
        for name, col in additions:
            if name not in cols:
                op.add_column("projects", col)

        # FKs / indexes (idempotent-ish)
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("projects")}
        for col, fk_name in [
            ("reopened_by", "fk_projects_reopened_by"),
            ("development_completed_by", "fk_projects_development_completed_by"),
            ("client_accepted_by", "fk_projects_client_accepted_by"),
            ("project_closed_by", "fk_projects_project_closed_by"),
        ]:
            if col in {c["name"] for c in inspector.get_columns("projects")} and fk_name not in existing_fks:
                try:
                    op.create_foreign_key(
                        fk_name, "projects", "users", [col], ["id"], ondelete="SET NULL"
                    )
                except Exception:
                    pass
        existing_ix = {ix["name"] for ix in inspector.get_indexes("projects")}
        if "ix_projects_reopened_by" not in existing_ix and "reopened_by" in {
            c["name"] for c in inspector.get_columns("projects")
        }:
            op.create_index("ix_projects_reopened_by", "projects", ["reopened_by"])

    if "project_status_events" not in tables:
        op.create_table(
            "project_status_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("from_status", sa.String(length=20), nullable=False),
            sa.Column("to_status", sa.String(length=20), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("actor_id", sa.String(length=36), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        )
        op.create_index(
            "ix_project_status_events_project_id", "project_status_events", ["project_id"]
        )
        op.create_index(
            "ix_project_status_events_project_created",
            "project_status_events",
            ["project_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "project_status_events" in tables:
        op.drop_table("project_status_events")
    if "projects" in tables:
        cols = {c["name"] for c in inspector.get_columns("projects")}
        for name in [
            "closed_reason",
            "project_closed_by",
            "project_closed_at",
            "client_accepted_by",
            "client_accepted_at",
            "development_completed_by",
            "development_completed_at",
            "reopened_by",
            "reopened_at",
            "status_reason",
        ]:
            if name in cols:
                op.drop_column("projects", name)
