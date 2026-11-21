"""036 project_phase_notes — optional note per delivery phase

Revision ID: s6t7u8v9w0x1
Revises: r5s6t7u8v9w0
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s6t7u8v9w0x1"
down_revision: Union[str, Sequence[str], None] = "r5s6t7u8v9w0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "project_phase_notes" in tables:
        return

    op.create_table(
        "project_phase_notes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("stage_number", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=36), nullable=True),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "stage_number", name="uq_project_phase_note"),
    )
    op.create_index("ix_project_phase_notes_id", "project_phase_notes", ["id"])
    op.create_index("ix_project_phase_notes_project_id", "project_phase_notes", ["project_id"])
    op.create_index("ix_project_phase_notes_stage_number", "project_phase_notes", ["stage_number"])
    op.create_index(
        "ix_project_phase_notes_project_stage",
        "project_phase_notes",
        ["project_id", "stage_number"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "project_phase_notes" not in tables:
        return
    op.drop_index("ix_project_phase_notes_project_stage", table_name="project_phase_notes")
    op.drop_index("ix_project_phase_notes_stage_number", table_name="project_phase_notes")
    op.drop_index("ix_project_phase_notes_project_id", table_name="project_phase_notes")
    op.drop_index("ix_project_phase_notes_id", table_name="project_phase_notes")
    op.drop_table("project_phase_notes")
