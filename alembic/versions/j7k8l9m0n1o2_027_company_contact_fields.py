"""027_company_contact_fields

Add company phone, website, and tagline for invoice document header.

Revision ID: j7k8l9m0n1o2
Revises: i6j7k8l9m0n1
Create Date: 2026-11-19 19:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "j7k8l9m0n1o2"
down_revision: Union[str, Sequence[str], None] = "i6j7k8l9m0n1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("company_phone", sa.String(length=50), nullable=True))
    op.add_column("settings", sa.Column("company_website", sa.String(length=255), nullable=True))
    op.add_column(
        "settings",
        sa.Column(
            "company_tagline",
            sa.String(length=255),
            nullable=True,
            server_default="Web Development | Cloud | Software Solutions",
        ),
    )


def downgrade() -> None:
    op.drop_column("settings", "company_tagline")
    op.drop_column("settings", "company_website")
    op.drop_column("settings", "company_phone")
