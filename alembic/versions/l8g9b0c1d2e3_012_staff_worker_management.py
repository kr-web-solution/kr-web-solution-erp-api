"""012_staff_worker_management

Staff profile fields on users plus tasks, attendance, holidays, leave,
expenses, documents, announcements, and payslips.

Revision ID: l8g9b0c1d2e3
Revises: k7f8a9b0c1d2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "l8g9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "k7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    user_cols = {c["name"] for c in inspector.get_columns("users")} if "users" in tables else set()

    def add_user_col(name: str, col: sa.Column) -> None:
        if name not in user_cols:
            op.add_column("users", col)

    add_user_col("department", sa.Column("department", sa.String(length=80), nullable=True))
    add_user_col("job_title", sa.Column("job_title", sa.String(length=120), nullable=True))
    add_user_col("reports_to_user_id", sa.Column("reports_to_user_id", sa.String(length=36), nullable=True))
    add_user_col("joined_on", sa.Column("joined_on", sa.Date(), nullable=True))
    add_user_col("phone", sa.Column("phone", sa.String(length=50), nullable=True))
    add_user_col("address", sa.Column("address", sa.Text(), nullable=True))
    add_user_col("bank_account", sa.Column("bank_account", sa.String(length=64), nullable=True))
    add_user_col("bank_ifsc", sa.Column("bank_ifsc", sa.String(length=20), nullable=True))
    add_user_col("pending_bank_account", sa.Column("pending_bank_account", sa.String(length=64), nullable=True))
    add_user_col("pending_bank_ifsc", sa.Column("pending_bank_ifsc", sa.String(length=20), nullable=True))
    add_user_col("monthly_gross_paise", sa.Column("monthly_gross_paise", sa.BigInteger(), nullable=True))
    add_user_col(
        "casual_leave_days",
        sa.Column("casual_leave_days", sa.Integer(), server_default="12", nullable=False),
    )
    add_user_col(
        "sick_leave_days",
        sa.Column("sick_leave_days", sa.Integer(), server_default="6", nullable=False),
    )

    # Refresh columns after adds for FK/index checks
    user_cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    user_fks = {fk["name"] for fk in sa.inspect(bind).get_foreign_keys("users")}
    user_indexes = {ix["name"] for ix in sa.inspect(bind).get_indexes("users")}

    if "reports_to_user_id" in user_cols and "fk_users_reports_to_user_id" not in user_fks:
        op.create_foreign_key(
            "fk_users_reports_to_user_id",
            "users",
            "users",
            ["reports_to_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "ix_users_department" not in user_indexes and "department" in user_cols:
        op.create_index("ix_users_department", "users", ["department"])
    if "ix_users_reports_to_user_id" not in user_indexes and "reports_to_user_id" in user_cols:
        op.create_index("ix_users_reports_to_user_id", "users", ["reports_to_user_id"])

    tables = set(sa.inspect(bind).get_table_names())

    if "work_tasks" not in tables:
        op.create_table(
            "work_tasks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("work_date", sa.Date(), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=True),
            sa.Column("assignee_user_id", sa.String(length=36), nullable=False),
            sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=20), server_default="todo", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_work_tasks_id", "work_tasks", ["id"])
        op.create_index("ix_work_tasks_work_date", "work_tasks", ["work_date"])
        op.create_index("ix_work_tasks_project_id", "work_tasks", ["project_id"])
        op.create_index("ix_work_tasks_assignee_user_id", "work_tasks", ["assignee_user_id"])
        op.create_index("ix_work_tasks_status", "work_tasks", ["status"])

    if "attendance_days" not in tables:
        op.create_table(
            "attendance_days",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("work_date", sa.Date(), nullable=False),
            sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("correction_reason", sa.String(length=20), nullable=True),
            sa.Column("correction_note", sa.Text(), nullable=True),
            sa.Column("correction_status", sa.String(length=20), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "work_date", name="uq_attendance_user_date"),
        )
        op.create_index("ix_attendance_days_id", "attendance_days", ["id"])
        op.create_index("ix_attendance_days_user_id", "attendance_days", ["user_id"])
        op.create_index("ix_attendance_days_work_date", "attendance_days", ["work_date"])
        op.create_index("ix_attendance_days_correction_status", "attendance_days", ["correction_status"])

    if "company_holidays" not in tables:
        op.create_table(
            "company_holidays",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("holiday_date", sa.Date(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("holiday_date", name="uq_company_holiday_date"),
        )
        op.create_index("ix_company_holidays_id", "company_holidays", ["id"])
        op.create_index("ix_company_holidays_holiday_date", "company_holidays", ["holiday_date"])

    if "leave_requests" not in tables:
        op.create_table(
            "leave_requests",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("leave_type", sa.String(length=20), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("half_day", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
            sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_leave_requests_id", "leave_requests", ["id"])
        op.create_index("ix_leave_requests_user_id", "leave_requests", ["user_id"])
        op.create_index("ix_leave_requests_leave_type", "leave_requests", ["leave_type"])
        op.create_index("ix_leave_requests_start_date", "leave_requests", ["start_date"])
        op.create_index("ix_leave_requests_end_date", "leave_requests", ["end_date"])
        op.create_index("ix_leave_requests_status", "leave_requests", ["status"])

    if "expense_claims" not in tables:
        op.create_table(
            "expense_claims",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("expense_date", sa.Date(), nullable=False),
            sa.Column("category", sa.String(length=20), nullable=False),
            sa.Column("amount_paise", sa.BigInteger(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("receipt_object_key", sa.String(length=500), nullable=True),
            sa.Column("receipt_original_filename", sa.String(length=255), nullable=True),
            sa.Column("receipt_content_type", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
            sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_expense_claims_id", "expense_claims", ["id"])
        op.create_index("ix_expense_claims_user_id", "expense_claims", ["user_id"])
        op.create_index("ix_expense_claims_expense_date", "expense_claims", ["expense_date"])
        op.create_index("ix_expense_claims_category", "expense_claims", ["category"])
        op.create_index("ix_expense_claims_status", "expense_claims", ["status"])

    if "staff_documents" not in tables:
        op.create_table(
            "staff_documents",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("kind", sa.String(length=20), server_default="other", nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("stored_filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=120), nullable=False),
            sa.Column("size_bytes", sa.Integer(), server_default="0", nullable=False),
            sa.Column("uploaded_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_staff_documents_id", "staff_documents", ["id"])
        op.create_index("ix_staff_documents_user_id", "staff_documents", ["user_id"])
        op.create_index("ix_staff_documents_kind", "staff_documents", ["kind"])

    if "announcements" not in tables:
        op.create_table(
            "announcements",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_announcements_id", "announcements", ["id"])

    if "payslips" not in tables:
        op.create_table(
            "payslips",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("month_key", sa.String(length=7), nullable=False),
            sa.Column("gross_paise", sa.BigInteger(), nullable=False),
            sa.Column("deductions_paise", sa.BigInteger(), server_default="0", nullable=False),
            sa.Column("net_paise", sa.BigInteger(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("pdf_object_key", sa.String(length=500), nullable=True),
            sa.Column("published_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "month_key", name="uq_payslip_user_month"),
        )
        op.create_index("ix_payslips_id", "payslips", ["id"])
        op.create_index("ix_payslips_user_id", "payslips", ["user_id"])
        op.create_index("ix_payslips_month_key", "payslips", ["month_key"])


def downgrade() -> None:
    op.drop_table("payslips")
    op.drop_table("announcements")
    op.drop_table("staff_documents")
    op.drop_table("expense_claims")
    op.drop_table("leave_requests")
    op.drop_table("company_holidays")
    op.drop_table("attendance_days")
    op.drop_table("work_tasks")

    op.drop_index("ix_users_reports_to_user_id", table_name="users")
    op.drop_index("ix_users_department", table_name="users")
    op.drop_constraint("fk_users_reports_to_user_id", "users", type_="foreignkey")
    for col in (
        "sick_leave_days",
        "casual_leave_days",
        "monthly_gross_paise",
        "pending_bank_ifsc",
        "pending_bank_account",
        "bank_ifsc",
        "bank_account",
        "address",
        "phone",
        "joined_on",
        "reports_to_user_id",
        "job_title",
        "department",
    ):
        op.drop_column("users", col)
