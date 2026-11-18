"""Expandable lookup / type / status tables (PK id, unique code)."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import String, Integer, Boolean, Text, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .core.database import Base, SoftDeleteMixin, TimestampMixin


class LookupBase(SoftDeleteMixin, TimestampMixin, Base):
    """Abstract lookup row shape."""

    __abstract__ = True

    code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PartyType(LookupBase):
    __tablename__ = "party_types"
    __table_args__ = (UniqueConstraint("code", name="uq_party_types_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"pty_{uuid.uuid4().hex[:10]}")


class InvoiceLedger(LookupBase):
    __tablename__ = "invoice_ledgers"
    __table_args__ = (UniqueConstraint("code", name="uq_invoice_ledgers_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"ild_{uuid.uuid4().hex[:10]}")


class InvoiceType(LookupBase):
    __tablename__ = "invoice_types"
    __table_args__ = (UniqueConstraint("code", name="uq_invoice_types_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"itp_{uuid.uuid4().hex[:10]}")
    service_code: Mapped[str] = mapped_column(String(10), nullable=False, default="SVC")
    default_ledger_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("invoice_ledgers.id", ondelete="SET NULL"), nullable=True
    )


class InvoiceStatus(LookupBase):
    __tablename__ = "invoice_statuses"
    __table_args__ = (UniqueConstraint("code", name="uq_invoice_statuses_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"ist_{uuid.uuid4().hex[:10]}")


class EmailDeliveryStatus(LookupBase):
    __tablename__ = "email_delivery_statuses"
    __table_args__ = (UniqueConstraint("code", name="uq_email_delivery_statuses_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"eds_{uuid.uuid4().hex[:10]}")


class ProjectType(LookupBase):
    __tablename__ = "project_types"
    __table_args__ = (UniqueConstraint("code", name="uq_project_types_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"ptp_{uuid.uuid4().hex[:10]}")


class ProjectStatus(LookupBase):
    __tablename__ = "project_statuses"
    __table_args__ = (UniqueConstraint("code", name="uq_project_statuses_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"pst_{uuid.uuid4().hex[:10]}")


class PipelineStage(LookupBase):
    __tablename__ = "pipeline_stages"
    __table_args__ = (UniqueConstraint("code", name="uq_pipeline_stages_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"pps_{uuid.uuid4().hex[:10]}")
    stage_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)


class EngagementKind(LookupBase):
    __tablename__ = "engagement_kinds"
    __table_args__ = (UniqueConstraint("code", name="uq_engagement_kinds_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"egk_{uuid.uuid4().hex[:10]}")


class EngagementStatus(LookupBase):
    __tablename__ = "engagement_statuses"
    __table_args__ = (UniqueConstraint("code", name="uq_engagement_statuses_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"egs_{uuid.uuid4().hex[:10]}")


class EnquirySource(LookupBase):
    __tablename__ = "enquiry_sources"
    __table_args__ = (UniqueConstraint("code", name="uq_enquiry_sources_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"eqs_{uuid.uuid4().hex[:10]}")


class LostReason(LookupBase):
    __tablename__ = "lost_reasons"
    __table_args__ = (UniqueConstraint("code", name="uq_lost_reasons_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"lsr_{uuid.uuid4().hex[:10]}")


class LeadStatus(LookupBase):
    __tablename__ = "lead_statuses"
    __table_args__ = (UniqueConstraint("code", name="uq_lead_statuses_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"lds_{uuid.uuid4().hex[:10]}")


class LeadSource(LookupBase):
    __tablename__ = "lead_sources"
    __table_args__ = (UniqueConstraint("code", name="uq_lead_sources_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"ldc_{uuid.uuid4().hex[:10]}")


class DocumentKind(LookupBase):
    __tablename__ = "document_kinds"
    __table_args__ = (
        UniqueConstraint("code", "scope", name="uq_document_kinds_code_scope"),
        Index("ix_document_kinds_scope", "scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"dok_{uuid.uuid4().hex[:10]}")
    scope: Mapped[str] = mapped_column(String(30), nullable=False, default="project")


class FeatureStatus(LookupBase):
    __tablename__ = "feature_statuses"
    __table_args__ = (UniqueConstraint("code", name="uq_feature_statuses_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"fst_{uuid.uuid4().hex[:10]}")


class LeaveType(LookupBase):
    __tablename__ = "leave_types"
    __table_args__ = (UniqueConstraint("code", name="uq_leave_types_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"lvt_{uuid.uuid4().hex[:10]}")


class ApprovalStatus(LookupBase):
    __tablename__ = "approval_statuses"
    __table_args__ = (UniqueConstraint("code", name="uq_approval_statuses_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"aps_{uuid.uuid4().hex[:10]}")


class WorkTaskStatus(LookupBase):
    __tablename__ = "work_task_statuses"
    __table_args__ = (UniqueConstraint("code", name="uq_work_task_statuses_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"wts_{uuid.uuid4().hex[:10]}")


class ExpenseSection(LookupBase):
    __tablename__ = "expense_sections"
    __table_args__ = (UniqueConstraint("code", name="uq_expense_sections_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"exs_{uuid.uuid4().hex[:10]}")


class ExpenseCategory(LookupBase):
    __tablename__ = "expense_categories"
    __table_args__ = (UniqueConstraint("code", name="uq_expense_categories_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"exc_{uuid.uuid4().hex[:10]}")
    section_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("expense_sections.id", ondelete="SET NULL"), nullable=True, index=True
    )


class ExpenseClaimCategory(LookupBase):
    __tablename__ = "expense_claim_categories"
    __table_args__ = (UniqueConstraint("code", name="uq_expense_claim_categories_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"ecc_{uuid.uuid4().hex[:10]}")


class LateFeeType(LookupBase):
    __tablename__ = "late_fee_types"
    __table_args__ = (UniqueConstraint("code", name="uq_late_fee_types_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"lft_{uuid.uuid4().hex[:10]}")


class LateFeeStatus(LookupBase):
    __tablename__ = "late_fee_statuses"
    __table_args__ = (UniqueConstraint("code", name="uq_late_fee_statuses_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"lfs_{uuid.uuid4().hex[:10]}")


class AttendanceCorrectionReason(LookupBase):
    __tablename__ = "attendance_correction_reasons"
    __table_args__ = (UniqueConstraint("code", name="uq_attendance_correction_reasons_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: f"acr_{uuid.uuid4().hex[:10]}")
