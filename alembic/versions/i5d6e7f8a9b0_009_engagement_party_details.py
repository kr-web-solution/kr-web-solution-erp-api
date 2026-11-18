"""009_engagement_party_details

Revision ID: i5d6e7f8a9b0
Revises: h4c5d6e7f8a9
Create Date: 2026-11-13 23:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i5d6e7f8a9b0"
down_revision: Union[str, Sequence[str], None] = "h4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("engagements", sa.Column("designation", sa.String(length=120), nullable=True))
    op.add_column("engagements", sa.Column("contact_email", sa.String(length=255), nullable=True))
    op.add_column("engagements", sa.Column("contact_phone", sa.String(length=50), nullable=True))
    op.add_column("engagements", sa.Column("website", sa.String(length=255), nullable=True))
    op.add_column("engagements", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("engagements", sa.Column("city", sa.String(length=120), nullable=True))
    op.add_column("engagements", sa.Column("state", sa.String(length=120), nullable=True))
    op.add_column("engagements", sa.Column("pincode", sa.String(length=20), nullable=True))
    op.add_column("engagements", sa.Column("gstin", sa.String(length=50), nullable=True))


def downgrade() -> None:
    for col in (
        "gstin",
        "pincode",
        "state",
        "city",
        "address",
        "website",
        "contact_phone",
        "contact_email",
        "designation",
    ):
        op.drop_column("engagements", col)
