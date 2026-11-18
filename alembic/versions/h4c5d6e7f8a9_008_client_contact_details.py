"""008_client_contact_details

Revision ID: h4c5d6e7f8a9
Revises: g3b4c5d6e7f8
Create Date: 2026-11-13 23:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h4c5d6e7f8a9"
down_revision: Union[str, Sequence[str], None] = "g3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("clients", "primary_contact_name", existing_type=sa.String(255), nullable=True)
    op.alter_column("clients", "email", existing_type=sa.String(255), nullable=True)
    op.add_column("clients", sa.Column("designation", sa.String(length=120), nullable=True))
    op.add_column("clients", sa.Column("contact_email", sa.String(length=255), nullable=True))
    op.add_column("clients", sa.Column("contact_phone", sa.String(length=50), nullable=True))
    op.add_column("clients", sa.Column("website", sa.String(length=255), nullable=True))
    op.add_column("clients", sa.Column("city", sa.String(length=120), nullable=True))
    op.add_column("clients", sa.Column("state", sa.String(length=120), nullable=True))
    op.add_column("clients", sa.Column("pincode", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "pincode")
    op.drop_column("clients", "state")
    op.drop_column("clients", "city")
    op.drop_column("clients", "website")
    op.drop_column("clients", "contact_phone")
    op.drop_column("clients", "contact_email")
    op.drop_column("clients", "designation")
    op.alter_column("clients", "email", existing_type=sa.String(255), nullable=False)
    op.alter_column("clients", "primary_contact_name", existing_type=sa.String(255), nullable=False)
