"""041 change request lifecycle + checklist/feature events

Revision ID: x1y2z3a4b5c6
Revises: w0x1y2z3a4b5
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x1y2z3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "w0x1y2z3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "project_features" in tables:
        cols = {c["name"] for c in inspector.get_columns("project_features")}
        # Widen status column
        try:
            op.alter_column(
                "project_features",
                "status",
                existing_type=sa.String(length=20),
                type_=sa.String(length=32),
                existing_nullable=False,
                server_default="submitted",
            )
        except Exception:
            pass

        additions = [
            ("cr_number", sa.Column("cr_number", sa.String(length=40), nullable=True)),
            ("business_purpose", sa.Column("business_purpose", sa.Text(), nullable=True)),
            ("submitted_by", sa.Column("submitted_by", sa.String(length=36), nullable=True)),
            ("dev_hours", sa.Column("dev_hours", sa.Numeric(10, 2), nullable=True)),
            ("test_hours", sa.Column("test_hours", sa.Numeric(10, 2), nullable=True)),
            ("complexity", sa.Column("complexity", sa.String(length=20), nullable=True)),
            ("dev_cost_paise", sa.Column("dev_cost_paise", sa.BigInteger(), nullable=True)),
            ("expense_paise", sa.Column("expense_paise", sa.BigInteger(), nullable=True)),
            ("estimated_delivery_date", sa.Column("estimated_delivery_date", sa.Date(), nullable=True)),
            ("assignee_user_id", sa.Column("assignee_user_id", sa.String(length=36), nullable=True)),
            ("internal_notes", sa.Column("internal_notes", sa.Text(), nullable=True)),
            (
                "is_billable",
                sa.Column("is_billable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            ),
            ("cancel_reason", sa.Column("cancel_reason", sa.Text(), nullable=True)),
            ("rejection_reason", sa.Column("rejection_reason", sa.Text(), nullable=True)),
            (
                "client_accepted_at",
                sa.Column("client_accepted_at", sa.DateTime(timezone=True), nullable=True),
            ),
            ("client_accepted_by", sa.Column("client_accepted_by", sa.String(length=36), nullable=True)),
        ]
        cols = {c["name"] for c in inspector.get_columns("project_features")}
        for name, col in additions:
            if name not in cols:
                op.add_column("project_features", col)

        if "cr_number" not in {
            ix.get("name") for ix in inspector.get_indexes("project_features")
        }:
            try:
                op.create_index("ix_project_features_cr_number", "project_features", ["cr_number"], unique=True)
            except Exception:
                pass

        # Migrate legacy statuses
        op.execute(sa.text("UPDATE project_features SET status = 'submitted' WHERE status = 'requested'"))
        op.execute(sa.text("UPDATE project_features SET status = 'in_development' WHERE status = 'in_progress'"))
        op.execute(sa.text("UPDATE project_features SET status = 'completed' WHERE status = 'done'"))

        # Assign CR numbers for existing rows missing one
        op.execute(
            sa.text(
                """
                WITH numbered AS (
                  SELECT id,
                         'CR-' || to_char(COALESCE(created_at, now()), 'YYYY') || '-' ||
                         lpad((ROW_NUMBER() OVER (
                           PARTITION BY to_char(COALESCE(created_at, now()), 'YYYY')
                           ORDER BY created_at NULLS LAST, id
                         ))::text, 4, '0') AS new_cr
                  FROM project_features
                  WHERE cr_number IS NULL
                )
                UPDATE project_features f
                SET cr_number = n.new_cr
                FROM numbered n
                WHERE f.id = n.id
                """
            )
        )

    if "project_feature_events" not in tables:
        op.create_table(
            "project_feature_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("feature_id", sa.String(length=36), nullable=False),
            sa.Column("from_status", sa.String(length=32), nullable=False),
            sa.Column("to_status", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("actor_id", sa.String(length=36), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["feature_id"], ["project_features.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_project_feature_events_feature_id", "project_feature_events", ["feature_id"])
        op.create_index(
            "ix_project_feature_events_feature_created",
            "project_feature_events",
            ["feature_id", "created_at"],
        )

    if "project_checklist_events" not in tables:
        op.create_table(
            "project_checklist_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("item_id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("actor_id", sa.String(length=36), nullable=True),
            sa.Column("done_at_snapshot", sa.DateTime(timezone=True), nullable=True),
            sa.Column("done_by_snapshot", sa.String(length=36), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["item_id"], ["project_checklist_items.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        )
        op.create_index(
            "ix_project_checklist_events_item_created",
            "project_checklist_events",
            ["item_id", "created_at"],
        )

    # clients.is_active (P5)
    if "clients" in tables:
        cols = {c["name"] for c in inspector.get_columns("clients")}
        if "is_active" not in cols:
            op.add_column(
                "clients",
                sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            )

    # audit_logs reason / denorm (P6)
    if "audit_logs" in tables:
        cols = {c["name"] for c in inspector.get_columns("audit_logs")}
        if "reason" not in cols:
            op.add_column("audit_logs", sa.Column("reason", sa.Text(), nullable=True))
        if "project_id" not in cols:
            op.add_column("audit_logs", sa.Column("project_id", sa.String(length=36), nullable=True))
            op.create_index("ix_audit_logs_project_id", "audit_logs", ["project_id"])
        if "client_id" not in cols:
            op.add_column("audit_logs", sa.Column("client_id", sa.String(length=36), nullable=True))
            op.create_index("ix_audit_logs_client_id", "audit_logs", ["client_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "project_checklist_events" in tables:
        op.drop_table("project_checklist_events")
    if "project_feature_events" in tables:
        op.drop_table("project_feature_events")
