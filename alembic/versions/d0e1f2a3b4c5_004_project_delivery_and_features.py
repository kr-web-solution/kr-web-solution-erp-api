"""004_project_delivery_and_features

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-06 13:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return inspect(bind).has_table(table)


def _has_index(bind, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in inspect(bind).get_indexes(table))


def _has_fk(bind, table: str, name: str) -> bool:
    return any(fk.get("name") == name for fk in inspect(bind).get_foreign_keys(table))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "projects", "delivery_phase"):
        op.add_column("projects", sa.Column("delivery_phase", sa.Integer(), server_default="5", nullable=False))
    if not _has_column(bind, "projects", "engagement_id"):
        op.add_column("projects", sa.Column("engagement_id", sa.String(length=36), nullable=True))

    if not _has_index(bind, "projects", "ix_projects_delivery_phase"):
        op.create_index(op.f("ix_projects_delivery_phase"), "projects", ["delivery_phase"], unique=False)
    if not _has_index(bind, "projects", "ix_projects_engagement_id"):
        op.create_index(op.f("ix_projects_engagement_id"), "projects", ["engagement_id"], unique=False)
    if not _has_fk(bind, "projects", "fk_projects_engagement_id"):
        op.create_foreign_key(
            "fk_projects_engagement_id",
            "projects",
            "engagements",
            ["engagement_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not _has_table(bind, "project_checklist_items"):
        op.create_table(
            "project_checklist_items",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("stage_number", sa.Integer(), nullable=False),
            sa.Column("item_key", sa.String(length=80), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=False),
            sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
            sa.Column("is_done", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("done_by", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["done_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "stage_number", "item_key", name="uq_project_checklist_item"),
        )
    if _has_table(bind, "project_checklist_items"):
        if not _has_index(bind, "project_checklist_items", "ix_project_checklist_items_id"):
            op.create_index(op.f("ix_project_checklist_items_id"), "project_checklist_items", ["id"], unique=False)
        if not _has_index(bind, "project_checklist_items", "ix_project_checklist_items_project_id"):
            op.create_index(
                op.f("ix_project_checklist_items_project_id"), "project_checklist_items", ["project_id"], unique=False
            )
        if not _has_index(bind, "project_checklist_items", "ix_project_checklist_items_stage_number"):
            op.create_index(
                op.f("ix_project_checklist_items_stage_number"),
                "project_checklist_items",
                ["stage_number"],
                unique=False,
            )

    if not _has_table(bind, "project_features"):
        op.create_table(
            "project_features",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), server_default="requested", nullable=False),
            sa.Column("quoted_paise", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    if _has_table(bind, "project_features"):
        if not _has_index(bind, "project_features", "ix_project_features_id"):
            op.create_index(op.f("ix_project_features_id"), "project_features", ["id"], unique=False)
        if not _has_index(bind, "project_features", "ix_project_features_project_id"):
            op.create_index(op.f("ix_project_features_project_id"), "project_features", ["project_id"], unique=False)
        if not _has_index(bind, "project_features", "ix_project_features_status"):
            op.create_index(op.f("ix_project_features_status"), "project_features", ["status"], unique=False)

    if not _has_table(bind, "project_feature_docs"):
        op.create_table(
            "project_feature_docs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("feature_id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("body_or_url", sa.Text(), nullable=False),
            sa.Column("doc_type", sa.String(length=20), server_default="note", nullable=False),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["feature_id"], ["project_features.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    if _has_table(bind, "project_feature_docs"):
        if not _has_index(bind, "project_feature_docs", "ix_project_feature_docs_id"):
            op.create_index(op.f("ix_project_feature_docs_id"), "project_feature_docs", ["id"], unique=False)
        if not _has_index(bind, "project_feature_docs", "ix_project_feature_docs_feature_id"):
            op.create_index(
                op.f("ix_project_feature_docs_feature_id"), "project_feature_docs", ["feature_id"], unique=False
            )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "project_feature_docs"):
        op.drop_table("project_feature_docs")
    if _has_table(bind, "project_features"):
        op.drop_table("project_features")
    if _has_table(bind, "project_checklist_items"):
        op.drop_table("project_checklist_items")
    if _has_fk(bind, "projects", "fk_projects_engagement_id"):
        op.drop_constraint("fk_projects_engagement_id", "projects", type_="foreignkey")
    if _has_index(bind, "projects", "ix_projects_engagement_id"):
        op.drop_index(op.f("ix_projects_engagement_id"), table_name="projects")
    if _has_index(bind, "projects", "ix_projects_delivery_phase"):
        op.drop_index(op.f("ix_projects_delivery_phase"), table_name="projects")
    if _has_column(bind, "projects", "engagement_id"):
        op.drop_column("projects", "engagement_id")
    if _has_column(bind, "projects", "delivery_phase"):
        op.drop_column("projects", "delivery_phase")
