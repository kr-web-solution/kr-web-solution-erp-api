"""019_auth_invite_profile_flags

Revision ID: b8e1c2d3f4a5
Revises: a7340afd58ff
Create Date: 2026-11-18 16:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e1c2d3f4a5"
down_revision: Union[str, Sequence[str], None] = "a7340afd58ff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DUMMY_USER_EMAILS = (
    "admin@krwebsolution.com",
    "worker@krwebsolution.com",
    "client@asterretail.com",
    "founder@krwebsolution.com",
)


def upgrade() -> None:
    op.add_column("users", sa.Column("password_set_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("invite_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_users_password_set_at"), "users", ["password_set_at"], unique=False)
    op.create_index(op.f("ix_users_profile_completed_at"), "users", ["profile_completed_at"], unique=False)
    op.create_index(op.f("ix_users_invite_sent_at"), "users", ["invite_sent_at"], unique=False)

    conn = op.get_bind()

    # Soft-delete legacy seeded dummy logins first
    conn.execute(
        sa.text(
            """
            UPDATE users
            SET
              is_active = false,
              deleted_at = COALESCE(deleted_at, NOW())
            WHERE lower(email) IN (
              'admin@krwebsolution.com',
              'worker@krwebsolution.com',
              'client@asterretail.com',
              'founder@krwebsolution.com'
            )
              AND deleted_at IS NULL
            """
        )
    )

    # Remaining active users already had usable passwords from prior create/seed
    conn.execute(
        sa.text(
            """
            UPDATE users
            SET password_set_at = COALESCE(created_at, NOW())
            WHERE password_set_at IS NULL
              AND password_hash IS NOT NULL
              AND deleted_at IS NULL
            """
        )
    )

    # Soft-delete Aster demo client if no active linked users remain
    conn.execute(
        sa.text(
            """
            UPDATE clients
            SET deleted_at = COALESCE(deleted_at, NOW())
            WHERE id = 'cli_aster'
              AND deleted_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM users
                WHERE client_id = 'cli_aster'
                  AND deleted_at IS NULL
                  AND is_active = true
              )
            """
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_invite_sent_at"), table_name="users")
    op.drop_index(op.f("ix_users_profile_completed_at"), table_name="users")
    op.drop_index(op.f("ix_users_password_set_at"), table_name="users")
    op.drop_column("users", "invite_sent_at")
    op.drop_column("users", "profile_completed_at")
    op.drop_column("users", "password_set_at")
