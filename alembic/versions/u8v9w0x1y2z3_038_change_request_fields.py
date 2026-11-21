"""038 change request fields + feature attachment columns

Revision ID: u8v9w0x1y2z3
Revises: t7u8v9w0x1y2
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u8v9w0x1y2z3"
down_revision: Union[str, Sequence[str], None] = "t7u8v9w0x1y2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "project_features" in tables:
        cols = {c["name"] for c in inspector.get_columns("project_features")}
        if "request_type" not in cols:
            op.add_column(
                "project_features",
                sa.Column(
                    "request_type",
                    sa.String(length=40),
                    nullable=False,
                    server_default="new_feature",
                ),
            )
            op.create_index(
                "ix_project_features_request_type",
                "project_features",
                ["request_type"],
            )
        if "expected_functionality" not in cols:
            op.add_column(
                "project_features",
                sa.Column("expected_functionality", sa.Text(), nullable=True),
            )
        if "priority" not in cols:
            op.add_column(
                "project_features",
                sa.Column(
                    "priority",
                    sa.String(length=20),
                    nullable=False,
                    server_default="medium",
                ),
            )
            op.create_index("ix_project_features_priority", "project_features", ["priority"])
        if "expected_date" not in cols:
            op.add_column(
                "project_features",
                sa.Column("expected_date", sa.Date(), nullable=True),
            )
        if "acceptance_criteria" not in cols:
            op.add_column(
                "project_features",
                sa.Column("acceptance_criteria", sa.Text(), nullable=True),
            )

    if "project_feature_docs" in tables:
        cols = {c["name"] for c in inspector.get_columns("project_feature_docs")}
        if "original_filename" not in cols:
            op.add_column(
                "project_feature_docs",
                sa.Column("original_filename", sa.String(length=255), nullable=True),
            )
        if "stored_filename" not in cols:
            op.add_column(
                "project_feature_docs",
                sa.Column("stored_filename", sa.String(length=255), nullable=True),
            )
        if "content_type" not in cols:
            op.add_column(
                "project_feature_docs",
                sa.Column("content_type", sa.String(length=120), nullable=True),
            )
        if "size_bytes" not in cols:
            op.add_column(
                "project_feature_docs",
                sa.Column("size_bytes", sa.Integer(), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "project_feature_docs" in tables:
        cols = {c["name"] for c in inspector.get_columns("project_feature_docs")}
        for col in ("size_bytes", "content_type", "stored_filename", "original_filename"):
            if col in cols:
                op.drop_column("project_feature_docs", col)

    if "project_features" in tables:
        cols = {c["name"] for c in inspector.get_columns("project_features")}
        if "acceptance_criteria" in cols:
            op.drop_column("project_features", "acceptance_criteria")
        if "expected_date" in cols:
            op.drop_column("project_features", "expected_date")
        if "priority" in cols:
            op.drop_index("ix_project_features_priority", table_name="project_features")
            op.drop_column("project_features", "priority")
        if "expected_functionality" in cols:
            op.drop_column("project_features", "expected_functionality")
        if "request_type" in cols:
            op.drop_index("ix_project_features_request_type", table_name="project_features")
            op.drop_column("project_features", "request_type")
