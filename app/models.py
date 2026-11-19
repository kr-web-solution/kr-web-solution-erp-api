import uuid
from datetime import datetime, date, timezone
from typing import Optional, List, Any
from sqlalchemy import (
    String, Integer, BigInteger, Numeric, ForeignKey,
    Text, Boolean, DateTime, Date, JSON, func, UniqueConstraint, Index, CheckConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .core.database import Base, SoftDeleteMixin, TimestampMixin
from .models_lookups import (  # noqa: F401 — register lookup metadata
    PartyType, InvoiceLedger, InvoiceType as InvoiceTypeLookup, InvoiceStatus as InvoiceStatusLookup,
    EmailDeliveryStatus, ProjectType, ProjectStatus as ProjectStatusLookup, PipelineStage,
    EngagementKind as EngagementKindLookup, EngagementStatus as EngagementStatusLookup,
    EnquirySource as EnquirySourceLookup, LostReason as LostReasonLookup,
    LeadStatus as LeadStatusLookup, LeadSource, DocumentKind, FeatureStatus,
    LeaveType, ApprovalStatus, WorkTaskStatus, ExpenseSection, ExpenseCategory,
    ExpenseClaimCategory, LateFeeType, LateFeeStatus, AttendanceCorrectionReason,
)


class Role(SoftDeleteMixin, TimestampMixin, Base):
    """Access roles (admin / founder / worker / client / lead)."""

    __tablename__ = "roles"
    __table_args__ = (
        Index("ix_roles_party_type", "party_type"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"rol_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    party_type_id: Mapped[str] = mapped_column(
        ForeignKey("party_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # denormalized party_types.code for fast filters
    party_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    users: Mapped[List["User"]] = relationship("User", back_populates="role_row")
    party_type_row: Mapped["PartyType"] = relationship("PartyType", foreign_keys=[party_type_id])


class User(SoftDeleteMixin, TimestampMixin, Base):
    """
    All login people live here.
    - role_id → roles (access)
    - party_type: staff | client | lead (who they are)
    - client_id / lead_id: org or lead record when party is client/lead
    """

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_role_active", "role", "is_active"),
        Index("ix_users_party_type_active", "party_type", "is_active"),
        Index("ix_users_role_id_active", "role_id", "is_active"),
        CheckConstraint(
            "party_type IN ('staff', 'client', 'lead')",
            name="ck_users_party_type",
        ),
        CheckConstraint(
            "(party_type <> 'client') OR (client_id IS NOT NULL)",
            name="ck_users_client_requires_client_id",
        ),
        CheckConstraint(
            "(party_type <> 'lead') OR (lead_id IS NOT NULL)",
            name="ck_users_lead_requires_lead_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"usr_{uuid.uuid4().hex[:10]}",
        index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Combine with roles table (role = denormalized code for fast filters / JWT)
    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True
    )  # mirrors roles.code
    party_type_id: Mapped[str] = mapped_column(
        ForeignKey("party_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    party_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="staff", index=True
    )  # denormalized party_types.code

    client_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lead_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Person / staff profile (all user data on users)
    department: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    designation: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    reports_to_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    joined_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bank_account: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    bank_ifsc: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pending_bank_account: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pending_bank_ifsc: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    monthly_gross_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    hourly_rate_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    casual_leave_days: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    sick_leave_days: Mapped[int] = mapped_column(Integer, default=6, nullable=False)

    # Auth / invite / progressive profile
    password_set_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    profile_completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    invite_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Relationships
    role_row: Mapped["Role"] = relationship("Role", back_populates="users")
    party_type_row: Mapped["PartyType"] = relationship("PartyType", foreign_keys=[party_type_id])
    client: Mapped[Optional["Client"]] = relationship(
        "Client", back_populates="users", foreign_keys=[client_id]
    )
    lead: Mapped[Optional["Lead"]] = relationship(
        "Lead", back_populates="portal_users", foreign_keys=[lead_id]
    )
    assigned_projects: Mapped[List["Project"]] = relationship(
        "Project", secondary="project_workers", back_populates="workers"
    )
    manager: Mapped[Optional["User"]] = relationship(
        "User", remote_side=[id], foreign_keys=[reports_to_user_id]
    )


class Client(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"cli_{uuid.uuid4().hex[:10]}",
        index=True
    )
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    primary_contact_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    designation: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)  # company / official email
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # company / official phone
    website: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    users: Mapped[List["User"]] = relationship("User", back_populates="client")
    projects: Mapped[List["Project"]] = relationship(
        "Project",
        secondary="project_clients",
        back_populates="clients",
    )
    invoices: Mapped[List["Invoice"]] = relationship("Invoice", back_populates="client", cascade="all, delete-orphan")
    recurring_invoices: Mapped[List["RecurringInvoice"]] = relationship("RecurringInvoice", back_populates="client", cascade="all, delete-orphan")


class Lead(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"led_{uuid.uuid4().hex[:10]}",
        index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("lead_sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(50), default="website", nullable=False)
    status_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("lead_statuses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="new", index=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    portal_users: Mapped[List["User"]] = relationship(
        "User", back_populates="lead", foreign_keys="User.lead_id"
    )


class Project(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_client_status", "client_id", "status"),
        Index("ix_projects_status_updated", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"prj_{uuid.uuid4().hex[:10]}",
        index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    budget_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    development_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    advance_invoice_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL", use_alter=True, name="fk_projects_advance_invoice_id"),
        nullable=True,
    )
    balance_invoice_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL", use_alter=True, name="fk_projects_balance_invoice_id"),
        nullable=True,
    )
    go_live_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    project_type_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("project_types.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_type: Mapped[str] = mapped_column(String(40), default="custom", nullable=False, index=True)
    status_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("project_statuses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="running", index=True, nullable=False)
    pipeline_stage_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pipeline_stages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    delivery_phase: Mapped[int] = mapped_column(Integer, default=5, nullable=False, index=True)  # 5–11
    engagement_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("engagements.id", ondelete="SET NULL", use_alter=True, name="fk_projects_engagement_id"),
        nullable=True,
        index=True,
    )

    # Relationships
    # client_id = primary/billing client (always one of `clients`; required ≥1 via project_clients)
    client: Mapped["Client"] = relationship("Client", foreign_keys=[client_id])
    clients: Mapped[List["Client"]] = relationship(
        "Client",
        secondary="project_clients",
        back_populates="projects",
        lazy="selectin",
    )
    workers: Mapped[List["User"]] = relationship("User", secondary="project_workers", back_populates="assigned_projects")
    # No cascade delete-orphan: children must be removed explicitly before project delete.
    invoices: Mapped[List["Invoice"]] = relationship(
        "Invoice",
        back_populates="project",
        foreign_keys="Invoice.project_id",
    )
    recurring_invoices: Mapped[List["RecurringInvoice"]] = relationship(
        "RecurringInvoice",
        back_populates="project",
    )

    @property
    def maintenance_plans(self) -> List["RecurringInvoice"]:
        """Domain alias: recurring maintenance plans for this project."""
        return list(self.recurring_invoices or [])
    checklist_items: Mapped[List["ProjectChecklistItem"]] = relationship(
        "ProjectChecklistItem",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectChecklistItem.stage_number, ProjectChecklistItem.sort_order",
    )
    features: Mapped[List["ProjectFeature"]] = relationship(
        "ProjectFeature",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectFeature.created_at.desc()",
    )
    documents: Mapped[List["ProjectDocument"]] = relationship(
        "ProjectDocument",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectDocument.created_at.desc()",
    )


class ProjectClient(Base):
    """Many-to-many: project ↔ clients. A project must keep ≥1 client."""

    __tablename__ = "project_clients"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProjectWorker(SoftDeleteMixin, Base):
    __tablename__ = "project_workers"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class ProjectChecklistItem(SoftDeleteMixin, Base):
    __tablename__ = "project_checklist_items"
    __table_args__ = (
        UniqueConstraint("project_id", "stage_number", "item_key", name="uq_project_checklist_item"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"pci_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    item_key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    done_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="checklist_items")
    done_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[done_by])


class ProjectFeature(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "project_features"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"pft_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="requested", nullable=False, index=True
    )  # requested, approved, in_progress, done, rejected
    quoted_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="features")
    docs: Mapped[List["ProjectFeatureDoc"]] = relationship(
        "ProjectFeatureDoc",
        back_populates="feature",
        cascade="all, delete-orphan",
        order_by="ProjectFeatureDoc.created_at.desc()",
    )


class ProjectFeatureDoc(SoftDeleteMixin, Base):
    __tablename__ = "project_feature_docs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"pfd_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    feature_id: Mapped[str] = mapped_column(ForeignKey("project_features.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_or_url: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(20), default="note", nullable=False)  # note, link, spec
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    feature: Mapped["ProjectFeature"] = relationship("ProjectFeature", back_populates="docs")
    author: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by])


class ProjectDocument(SoftDeleteMixin, Base):
    """Quotation, agreement, and other files attached to a project."""

    __tablename__ = "project_documents"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"pdoc_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    doc_kind: Mapped[str] = mapped_column(String(30), default="other", nullable=False, index=True)
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="documents")
    uploader: Mapped[Optional["User"]] = relationship("User", foreign_keys=[uploaded_by])


class Invoice(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("recurring_id", "period_key", name="uq_invoice_recurring_period"),
        Index("ix_invoices_status_issue_date", "status", "issue_date"),
        Index("ix_invoices_client_status", "client_id", "status"),
        Index("ix_invoices_status_due_date", "status", "due_date"),
        Index("ix_invoices_paid_at", "paid_at"),
        Index("ix_invoices_ledger_status", "ledger", "status"),
        Index("ix_invoices_client_issue_date", "client_id", "issue_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"inv_{uuid.uuid4().hex[:10]}",
        index=True
    )
    invoice_number: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    invoice_type_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("invoice_types.id", ondelete="SET NULL"), nullable=True, index=True
    )
    invoice_type: Mapped[str] = mapped_column(String(20), default="custom_dev", nullable=False)
    ledger_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("invoice_ledgers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ledger: Mapped[str] = mapped_column(String(20), default="development", nullable=False, index=True)
    period_months: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    covers_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    covers_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    monthly_rate_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    recurring_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recurring_invoices.id", ondelete="SET NULL"), nullable=True, index=True)
    period_key: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)  # e.g. "2026-11"
    parent_invoice_id: Mapped[Optional[str]] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True, index=True)

    status_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("invoice_statuses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True, nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    subtotal_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tax_rate_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0.00, nullable=False)
    tax_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    upi_id_override: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    upi_qr_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Reminders & Email Delivery Tracking
    reminder_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    overdue_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    email_status_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("email_delivery_statuses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    email_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_emailed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    client: Mapped["Client"] = relationship("Client", back_populates="invoices")
    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="invoices",
        foreign_keys=[project_id],
    )
    recurring_invoice: Mapped[Optional["RecurringInvoice"]] = relationship("RecurringInvoice", foreign_keys=[recurring_id])
    parent_invoice: Mapped[Optional["Invoice"]] = relationship("Invoice", remote_side=[id], foreign_keys=[parent_invoice_id])
    items: Mapped[List["InvoiceItem"]] = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.id")
    late_fee_actions: Mapped[List["LateFeeAction"]] = relationship("LateFeeAction", foreign_keys="[LateFeeAction.invoice_id]", back_populates="invoice", cascade="all, delete-orphan")


class InvoiceAuditLog(SoftDeleteMixin, Base):
    __tablename__ = "invoice_audit_logs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"aud_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    old_subtotal_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    new_subtotal_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class InvoiceItem(SoftDeleteMixin, Base):
    __tablename__ = "invoice_items"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"itm_{uuid.uuid4().hex[:10]}",
        index=True
    )
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(10, 2), default=1.00, nullable=False)
    unit_rate_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Relationship
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="items")


class RecurringInvoice(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "recurring_invoices"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"rcr_{uuid.uuid4().hex[:10]}",
        index=True
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    send_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1-28
    due_after_days: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    reminder_every_days: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    ends_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    line_items_template: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    tax_rate_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0.00, nullable=False)
    day_of_month: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    next_run_date: Mapped[date] = mapped_column(Date, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships (MaintenancePlan == RecurringInvoice)
    client: Mapped["Client"] = relationship("Client", back_populates="recurring_invoices")
    project: Mapped["Project"] = relationship("Project", back_populates="recurring_invoices")


class LateFeeAction(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "late_fee_actions"
    __table_args__ = (
        Index("ix_late_fee_actions_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"lfa_{uuid.uuid4().hex[:10]}",
        index=True
    )
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    fee_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    fee_type: Mapped[str] = mapped_column(String(20), default="fixed", nullable=False)  # "fixed", "percent"
    fee_value: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), default="Payment not received by due date", nullable=False)
    send_on: Mapped[date] = mapped_column(Date, nullable=False)
    repeat_every_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reminder_every_hours: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # overdue follow-up cadence
    next_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    repeat_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True, nullable=False)  # "pending", "scheduled", "sent", "waived", "extended"
    decided_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    late_fee_invoice_id: Mapped[Optional[str]] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    invoice: Mapped["Invoice"] = relationship("Invoice", foreign_keys=[invoice_id], back_populates="late_fee_actions")
    decided_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[decided_by])
    late_fee_invoice: Mapped[Optional["Invoice"]] = relationship("Invoice", foreign_keys=[late_fee_invoice_id])



class Engagement(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "engagements"
    __table_args__ = (
        Index("ix_engagements_status_stage", "status", "current_stage"),
        Index("ix_engagements_client_status", "client_id", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"eng_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    designation: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # company email
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # company phone
    website: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    gstin: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    enquiry_source_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("enquiry_sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enquiry_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    engagement_kind_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("engagement_kinds.id", ondelete="SET NULL"), nullable=True, index=True
    )
    engagement_kind: Mapped[str] = mapped_column(String(20), default="project", nullable=False, index=True)
    pipeline_stage_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pipeline_stages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    current_stage: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    status_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("engagement_statuses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)
    estimated_budget_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    quoted_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    advance_paise: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    lost_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    lost_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warranty_ends_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    advance_invoice_id: Mapped[Optional[str]] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    final_invoice_id: Mapped[Optional[str]] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    recurring_invoice_id: Mapped[Optional[str]] = mapped_column(ForeignKey("recurring_invoices.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    checklist_items: Mapped[List["EngagementChecklistItem"]] = relationship(
        "EngagementChecklistItem",
        back_populates="engagement",
        cascade="all, delete-orphan",
        order_by="EngagementChecklistItem.stage_number, EngagementChecklistItem.sort_order",
    )
    documents: Mapped[List["EngagementDocument"]] = relationship(
        "EngagementDocument",
        back_populates="engagement",
        cascade="all, delete-orphan",
        order_by="EngagementDocument.created_at.desc()",
    )
    client: Mapped[Optional["Client"]] = relationship("Client", foreign_keys=[client_id])
    project: Mapped[Optional["Project"]] = relationship("Project", foreign_keys=[project_id])
    owner: Mapped[Optional["User"]] = relationship("User", foreign_keys=[owner_user_id])
    recurring_invoice: Mapped[Optional["RecurringInvoice"]] = relationship("RecurringInvoice", foreign_keys=[recurring_invoice_id])


class EngagementDocument(SoftDeleteMixin, Base):
    """Initial enquiry briefs, PDFs, and other sales attachments."""

    __tablename__ = "engagement_documents"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"edoc_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    doc_kind: Mapped[str] = mapped_column(String(30), default="enquiry", nullable=False, index=True)
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    engagement: Mapped["Engagement"] = relationship("Engagement", back_populates="documents")
    uploader: Mapped[Optional["User"]] = relationship("User", foreign_keys=[uploaded_by])


class EngagementChecklistItem(SoftDeleteMixin, Base):
    __tablename__ = "engagement_checklist_items"
    __table_args__ = (
        UniqueConstraint("engagement_id", "stage_number", "item_key", name="uq_engagement_checklist_item"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"eci_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    engagement_id: Mapped[str] = mapped_column(ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    item_key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    done_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    engagement: Mapped["Engagement"] = relationship("Engagement", back_populates="checklist_items")
    done_by_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[done_by])


class Note(SoftDeleteMixin, Base):
    __tablename__ = "notes"
    __table_args__ = (
        Index("ix_notes_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"not_{uuid.uuid4().hex[:10]}",
        index=True
    )
    entity_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)  # "client" or "lead"
    entity_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    author_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False
    )

    # Relationship
    author: Mapped[Optional["User"]] = relationship("User")


class Settings(SoftDeleteMixin, Base):
    __tablename__ = "settings"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    company_name: Mapped[str] = mapped_column(String(255), default="KR Web Solution", nullable=False)
    company_address: Mapped[Optional[str]] = mapped_column(Text, default="Salem, Tamil Nadu, India", nullable=True)
    company_gstin: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    company_logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Company code used in invoice numbers, e.g. KRWS → KRWS-DEV-2026-001
    invoice_prefix: Mapped[str] = mapped_column(String(32), default="KRWS", nullable=False)
    invoice_next_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Per service/year counters, e.g. {"DEV-2026": 2, "MNT-2026": 1}
    invoice_sequences: Mapped[Any] = mapped_column(JSON, default=dict, nullable=False)
    default_tax_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0.00, nullable=False)
    gst_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pf_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pf_employee_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0.00, nullable=False)
    esi_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    esi_employee_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0.00, nullable=False)
    pt_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pt_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    payment_terms: Mapped[Optional[str]] = mapped_column(Text, default="Payment due within 15 days of issue.", nullable=True)
    bank_beneficiary_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bank_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bank_account_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    bank_ifsc: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    bank_upi_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    bank_upi_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bank_upi_qr_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    smtp_host: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    smtp_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    smtp_password_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    smtp_from_name: Mapped[Optional[str]] = mapped_column(String(255), default="KR Web Solution", nullable=True)
    smtp_from_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    invoice_cc_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class EmailTemplate(SoftDeleteMixin, Base):
    __tablename__ = "email_templates"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # "invoice_sent", "payment_reminder", "lead_welcome", "password_reset"
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


# ----------------- Staff / Worker management -----------------


class WorkTask(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "work_tasks"
    __table_args__ = (
        Index("ix_work_tasks_assignee_date", "assignee_user_id", "work_date"),
        Index("ix_work_tasks_assignee_status", "assignee_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"wtk_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assignee_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="todo", nullable=False, index=True
    )  # todo / doing / done

    project: Mapped[Optional["Project"]] = relationship("Project", foreign_keys=[project_id])
    assignee: Mapped["User"] = relationship("User", foreign_keys=[assignee_user_id])
    created_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_user_id])


class AttendanceDay(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "attendance_days"
    __table_args__ = (
        UniqueConstraint("user_id", "work_date", name="uq_attendance_user_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"att_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    check_in_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    check_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    correction_reason: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # missed / wfh / on_duty
    correction_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    correction_status: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True
    )  # pending / approved / rejected

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])


class CompanyHoliday(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "company_holidays"
    __table_args__ = (
        UniqueConstraint("holiday_date", name="uq_company_holiday_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"hol_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class LeaveRequest(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        Index("ix_leave_requests_user_status", "user_id", "status"),
        Index("ix_leave_requests_status_dates", "status", "start_date", "end_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"lvr_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leave_type: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # casual / sick / unpaid
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    half_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False, index=True
    )  # pending / approved / rejected
    decided_by_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    decided_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[decided_by_user_id])


class ExpenseClaim(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "expense_claims"
    __table_args__ = (
        Index("ix_expense_claims_user_status", "user_id", "status"),
        Index("ix_expense_claims_status_date", "status", "expense_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"exp_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # travel / food / other
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    receipt_object_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    receipt_original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    receipt_content_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False, index=True
    )  # pending / approved / rejected / paid
    decided_by_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    decided_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[decided_by_user_id])


class StaffDocument(SoftDeleteMixin, Base):
    __tablename__ = "staff_documents"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"sdoc_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20), default="other", nullable=False, index=True
    )  # offer / id / other
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uploaded_by: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    uploader: Mapped[Optional["User"]] = relationship("User", foreign_keys=[uploaded_by])


class Announcement(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "announcements"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"ann_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[created_by_user_id])


class Payslip(SoftDeleteMixin, TimestampMixin, Base):
    __tablename__ = "payslips"
    __table_args__ = (
        UniqueConstraint("user_id", "month_key", name="uq_payslip_user_month"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"pay_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    month_key: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # YYYY-MM
    gross_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deductions_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    net_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pdf_object_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    published_by_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    published_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[published_by_user_id])


class CompanyExpense(SoftDeleteMixin, TimestampMixin, Base):
    """Studio / company spend ledger (not staff reimbursement claims)."""

    __tablename__ = "company_expenses"
    __table_args__ = (
        Index("ix_company_expenses_date_section", "expense_date", "section"),
        Index("ix_company_expenses_section_category", "section", "category"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"cexp_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    section_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("expense_sections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    section: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    category_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("expense_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    receipt_object_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    receipt_original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    receipt_content_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    renewal_interval_months: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # 1 / 3 / 6 / 12 for recurring
    next_renewal_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_by: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[created_by_user_id]
    )


class AuditLog(Base):
    """Immutable change history — founder/admin only. No soft delete."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_actor_created", "actor_id", "created_at"),
        Index("ix_audit_logs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: f"audg_{uuid.uuid4().hex[:10]}",
        index=True,
    )
    actor_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # create|update|soft_delete|restore
    changed_fields: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    before: Mapped[Any] = mapped_column(JSON, nullable=True)
    after: Mapped[Any] = mapped_column(JSON, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor: Mapped[Optional["User"]] = relationship("User", foreign_keys=[actor_id])

