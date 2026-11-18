"""003_engagements_pipeline

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-11-06 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "engagements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("contact_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("enquiry_source", sa.String(length=50), nullable=True),
        sa.Column("engagement_kind", sa.String(length=20), server_default="project", nullable=False),
        sa.Column("current_stage", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("estimated_budget_paise", sa.BigInteger(), nullable=True),
        sa.Column("quoted_paise", sa.BigInteger(), nullable=True),
        sa.Column("advance_paise", sa.BigInteger(), nullable=True),
        sa.Column("lost_reason", sa.String(length=50), nullable=True),
        sa.Column("lost_note", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("warranty_ends_on", sa.Date(), nullable=True),
        sa.Column("client_id", sa.String(length=36), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("advance_invoice_id", sa.String(length=36), nullable=True),
        sa.Column("final_invoice_id", sa.String(length=36), nullable=True),
        sa.Column("recurring_invoice_id", sa.String(length=36), nullable=True),
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["advance_invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["final_invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recurring_invoice_id"], ["recurring_invoices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_engagements_id"), "engagements", ["id"], unique=False)
    op.create_index(op.f("ix_engagements_title"), "engagements", ["title"], unique=False)
    op.create_index(op.f("ix_engagements_enquiry_source"), "engagements", ["enquiry_source"], unique=False)
    op.create_index(op.f("ix_engagements_engagement_kind"), "engagements", ["engagement_kind"], unique=False)
    op.create_index(op.f("ix_engagements_current_stage"), "engagements", ["current_stage"], unique=False)
    op.create_index(op.f("ix_engagements_status"), "engagements", ["status"], unique=False)
    op.create_index(op.f("ix_engagements_client_id"), "engagements", ["client_id"], unique=False)
    op.create_index(op.f("ix_engagements_project_id"), "engagements", ["project_id"], unique=False)
    op.create_index(op.f("ix_engagements_recurring_invoice_id"), "engagements", ["recurring_invoice_id"], unique=False)
    op.create_index(op.f("ix_engagements_owner_user_id"), "engagements", ["owner_user_id"], unique=False)

    op.create_table(
        "engagement_checklist_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("engagement_id", sa.String(length=36), nullable=False),
        sa.Column("stage_number", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(length=80), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_done", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["done_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("engagement_id", "stage_number", "item_key", name="uq_engagement_checklist_item"),
    )
    op.create_index(op.f("ix_engagement_checklist_items_id"), "engagement_checklist_items", ["id"], unique=False)
    op.create_index(op.f("ix_engagement_checklist_items_engagement_id"), "engagement_checklist_items", ["engagement_id"], unique=False)
    op.create_index(op.f("ix_engagement_checklist_items_stage_number"), "engagement_checklist_items", ["stage_number"], unique=False)


def downgrade() -> None:
    op.drop_table("engagement_checklist_items")
    op.drop_table("engagements")
