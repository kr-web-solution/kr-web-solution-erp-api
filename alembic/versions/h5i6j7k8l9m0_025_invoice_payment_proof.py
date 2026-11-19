"""025_invoice_payment_proof

Client-uploaded payment proof + founder verification metadata on invoices.

Revision ID: h5i6j7k8l9m0
Revises: g4h5i6j7k8l9
Create Date: 2026-11-19 18:15:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "h5i6j7k8l9m0"
down_revision: Union[str, Sequence[str], None] = "g4h5i6j7k8l9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("payment_proof_object_key", sa.String(length=500), nullable=True))
    op.add_column("invoices", sa.Column("payment_proof_filename", sa.String(length=255), nullable=True))
    op.add_column("invoices", sa.Column("payment_proof_content_type", sa.String(length=120), nullable=True))
    op.add_column("invoices", sa.Column("payment_proof_uploaded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "invoices",
        sa.Column("payment_proof_uploaded_by", sa.String(length=36), nullable=True),
    )
    op.add_column("invoices", sa.Column("payment_proof_note", sa.Text(), nullable=True))
    op.add_column("invoices", sa.Column("payment_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "invoices",
        sa.Column("payment_verified_by", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_invoices_payment_proof_uploaded_by_users",
        "invoices",
        "users",
        ["payment_proof_uploaded_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_invoices_payment_verified_by_users",
        "invoices",
        "users",
        ["payment_verified_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_invoices_payment_proof_uploaded_at",
        "invoices",
        ["payment_proof_uploaded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoices_payment_proof_uploaded_at", table_name="invoices")
    op.drop_constraint("fk_invoices_payment_verified_by_users", "invoices", type_="foreignkey")
    op.drop_constraint("fk_invoices_payment_proof_uploaded_by_users", "invoices", type_="foreignkey")
    op.drop_column("invoices", "payment_verified_by")
    op.drop_column("invoices", "payment_verified_at")
    op.drop_column("invoices", "payment_proof_note")
    op.drop_column("invoices", "payment_proof_uploaded_by")
    op.drop_column("invoices", "payment_proof_uploaded_at")
    op.drop_column("invoices", "payment_proof_content_type")
    op.drop_column("invoices", "payment_proof_filename")
    op.drop_column("invoices", "payment_proof_object_key")
