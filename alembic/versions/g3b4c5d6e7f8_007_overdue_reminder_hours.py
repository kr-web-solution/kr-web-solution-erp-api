"""007_overdue_reminder_hours

Revision ID: g3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-06 17:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g3b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "late_fee_actions",
        sa.Column("reminder_every_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "late_fee_actions",
        sa.Column("next_reminder_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("late_fee_actions", "next_reminder_at")
    op.drop_column("late_fee_actions", "reminder_every_hours")
