from datetime import datetime, date
from typing import Optional, List, Any
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

# ----------------- Base Config -----------------
class OrmBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

# ----------------- Auth & Users -----------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=20)


class RoleBrief(OrmBase):
    id: str
    code: str
    name: str
    party_type: str  # staff | client | lead
    description: Optional[str] = None


class ClientPartyBrief(OrmBase):
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    gstin: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None


class LeadPartyBrief(OrmBase):
    id: str
    name: str
    company: str
    contact_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    status: str


class UserResponse(OrmBase):
    id: str
    email: str
    full_name: str
    role: str  # roles.code
    role_id: str
    party_type: str  # staff | client | lead
    client_id: Optional[str] = None
    lead_id: Optional[str] = None
    is_active: bool
    phone: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[str] = None
    created_at: datetime
    role_info: Optional[RoleBrief] = None
    client: Optional[ClientPartyBrief] = None
    lead: Optional[LeadPartyBrief] = None
    password_set: bool = True
    profile_completed: bool = False
    missing_profile_fields: List[str] = Field(default_factory=list)
    # Present only after invite create in non-production
    dev_invite_link: Optional[str] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

class UserCreate(BaseModel):
    """Invite payload — no password; invite email sets it."""
    email: EmailStr
    full_name: str
    role: str  # admin | founder_ceo | worker | client | lead
    client_id: Optional[str] = None
    lead_id: Optional[str] = None
    phone: Optional[str] = None
    designation: Optional[str] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    lead_id: Optional[str] = None
    phone: Optional[str] = None
    designation: Optional[str] = None

class MeUpdate(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    current_password: Optional[str] = None


class MeProfileUpdate(BaseModel):
    """Progressive onboarding fields after invite."""
    full_name: Optional[str] = None
    phone: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[str] = None


class SetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class MicrosoftLoginRequest(BaseModel):
    id_token: str = Field(min_length=20)


class OAuthProvidersResponse(BaseModel):
    """Public OAuth client IDs for the SPA (no secrets)."""
    google_client_id: Optional[str] = None
    microsoft_client_id: Optional[str] = None
    microsoft_tenant_id: str = "common"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=6)

# ----------------- Clients -----------------
class ClientBase(BaseModel):
    name: str = Field(min_length=1)
    primary_contact_name: Optional[str] = None
    designation: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    email: Optional[str] = None  # company / official email
    phone: Optional[str] = None  # company / official phone
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    state_code: Optional[str] = None
    notes: Optional[str] = None

class ClientCreate(ClientBase):
    pass

class ClientUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    primary_contact_name: Optional[str] = None
    designation: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    state_code: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None

class ClientResponse(ClientBase, OrmBase):
    id: str
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
    projects_count: Optional[int] = 0
    invoices_count: Optional[int] = 0

# ----------------- Leads -----------------
class LeadBase(BaseModel):
    name: str
    company: str
    contact_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    source: str = "website"
    status: str = "new"  # "new", "contacted", "proposal", "won", "lost"
    notes: Optional[str] = None

class LeadCreate(LeadBase):
    pass

class LeadUpdate(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None

class LeadResponse(LeadBase, OrmBase):
    id: str
    created_at: datetime
    updated_at: datetime

class LeadConvertRequest(BaseModel):
    create_project: bool = True
    project_name: Optional[str] = None
    budget_paise: Optional[int] = 0

class LeadConvertResponse(BaseModel):
    client_id: str
    project_id: Optional[str] = None
    message: str

# ----------------- Projects -----------------
class MaintenanceBillingCreate(BaseModel):
    amount_paise: int = Field(gt=0)
    send_day: int = Field(ge=1, le=28, default=1)
    due_after_days: int = Field(ge=1, default=5)
    reminder_every_days: int = Field(ge=1, default=2)
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    notes: Optional[str] = None

class ProjectClientBrief(BaseModel):
    id: str
    name: str
    is_primary: bool = False

class ProjectClientAdd(BaseModel):
    client_id: str

class ProjectBase(BaseModel):
    name: str
    # Legacy single client (still accepted). Prefer client_ids for multi-client.
    client_id: Optional[str] = None
    client_ids: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_paise: Optional[int] = 0
    status: str = "running"  # "lead", "running", "on_hold", "stopped", "completed"

class ProjectCreate(ProjectBase):
    # At least one client is required — projects.client_id is NOT NULL in DB.
    client_ids: List[str] = Field(min_length=1)
    worker_ids: Optional[List[str]] = []
    development_days: Optional[int] = Field(None, ge=1)
    maintenance: Optional[MaintenanceBillingCreate] = None

    @model_validator(mode="before")
    @classmethod
    def coalesce_legacy_client_id(cls, data: Any) -> Any:
        """Accept legacy client_id-only payloads by folding into client_ids."""
        if not isinstance(data, dict):
            return data
        ids = [x for x in (data.get("client_ids") or []) if x]
        legacy = data.get("client_id")
        if legacy and legacy not in ids:
            ids = [legacy] + ids
        if not ids:
            raise ValueError("A project must have at least one client (client_id / client_ids).")
        data = {**data, "client_ids": ids, "client_id": ids[0]}
        return data

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    client_id: Optional[str] = None  # set primary client (must already be linked, or use client_ids)
    client_ids: Optional[List[str]] = None  # replace full membership (≥1)
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_paise: Optional[int] = None
    development_days: Optional[int] = Field(None, ge=1)
    advance_paise: Optional[int] = Field(None, ge=0)
    maintenance_amount_paise: Optional[int] = Field(None, ge=0)
    status: Optional[str] = None
    status_reason: Optional[str] = None
    reopen_reason: Optional[str] = None
    force_complete: Optional[bool] = False
    force_complete_reason: Optional[str] = None
    worker_ids: Optional[List[str]] = None

class ProjectMilestoneRequest(BaseModel):
    reason: Optional[str] = None

class ProjectResponse(OrmBase):
    id: str
    name: str
    client_id: str  # primary / billing client
    client_name: Optional[str] = None
    client_ids: List[str] = []
    clients: List[ProjectClientBrief] = []
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_paise: Optional[int] = None  # None for worker role
    development_days: Optional[int] = None
    advance_paise: Optional[int] = None
    balance_paise: Optional[int] = None  # budget - advance (commercial split)
    invoiced_paise: Optional[int] = None  # sum of project-invoice subtots (ex-GST)
    remaining_paise: Optional[int] = None  # max(budget - invoiced, 0)
    advance_invoice_id: Optional[str] = None
    balance_invoice_id: Optional[str] = None
    go_live_at: Optional[date] = None
    status: str
    status_reason: Optional[str] = None
    reopened_at: Optional[datetime] = None
    reopened_by: Optional[str] = None
    development_completed_at: Optional[datetime] = None
    development_completed_by: Optional[str] = None
    client_accepted_at: Optional[datetime] = None
    client_accepted_by: Optional[str] = None
    project_closed_at: Optional[datetime] = None
    project_closed_by: Optional[str] = None
    closed_reason: Optional[str] = None
    delivery_phase: int = 5
    delivery_phase_name: Optional[str] = None
    engagement_id: Optional[str] = None
    workers: List[UserResponse] = []
    maintenance_status: Optional[str] = None  # "active", "paused", "none"
    maintenance_amount_paise: Optional[int] = None
    maintenance_send_day: Optional[int] = None
    maintenance_due_after_days: Optional[int] = None
    maintenance_next_run_date: Optional[date] = None
    maintenance_prorata_invoice_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ProjectProrataResponse(BaseModel):
    created: bool
    skipped: bool = False
    emailed: bool = False
    reason: Optional[str] = None
    invoice_id: Optional[str] = None
    invoice_number: Optional[str] = None
    period_key: Optional[str] = None
    billable_days: Optional[int] = None
    days_in_month: Optional[int] = None
    amount_paise: Optional[int] = None
    next_run_date: Optional[date] = None
    project: Optional[ProjectResponse] = None


class ProjectGoLiveRequest(BaseModel):
    go_live_date: Optional[date] = None


class ProjectInvoiceCreateResponse(BaseModel):
    invoice_id: str
    invoice_number: str
    total_paise: int
    status: str
    kind: str  # advance | balance
    project: Optional[ProjectResponse] = None


class ProjectChecklistItemResponse(OrmBase):
    id: str
    project_id: str
    stage_number: int
    item_key: str
    label: str
    sort_order: int
    is_done: bool
    done_at: Optional[datetime] = None
    done_by: Optional[str] = None


class ProjectPhaseNoteResponse(OrmBase):
    stage_number: int
    body: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class ProjectPhaseNoteUpdate(BaseModel):
    """Optional free-text note for one delivery phase. Empty/null clears the note."""

    body: Optional[str] = None


class ProjectDeliveryDetailResponse(ProjectResponse):
    checklist_items: List[ProjectChecklistItemResponse] = []
    checklist_progress_percent: int = 0
    phase_notes: List[ProjectPhaseNoteResponse] = []


class ProjectFeatureDocCreate(BaseModel):
    title: str
    body_or_url: str
    doc_type: str = "note"  # note, link, spec


class ProjectDocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    description: Optional[str] = None
    doc_kind: Optional[str] = None


class ProjectDocumentResponse(OrmBase):
    id: str
    project_id: str
    title: str
    description: Optional[str] = None
    original_filename: str
    content_type: str
    size_bytes: int
    doc_kind: str
    uploaded_by: Optional[str] = None
    created_at: datetime


class ProjectFeatureDocResponse(OrmBase):
    id: str
    feature_id: str
    title: str
    body_or_url: str
    doc_type: str
    original_filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    created_by: Optional[str] = None
    created_at: datetime


class ProjectFeatureCreate(BaseModel):
    title: str
    description: Optional[str] = None
    business_purpose: Optional[str] = None
    request_type: str = "new_feature"
    expected_functionality: Optional[str] = None
    priority: str = "medium"
    expected_date: Optional[date] = None
    acceptance_criteria: Optional[str] = None
    status: str = "submitted"
    quoted_paise: Optional[int] = None
    is_billable: bool = True
    dev_hours: Optional[float] = None
    test_hours: Optional[float] = None
    complexity: Optional[str] = None
    dev_cost_paise: Optional[int] = None
    expense_paise: Optional[int] = None
    estimated_delivery_date: Optional[date] = None
    assignee_user_id: Optional[str] = None
    internal_notes: Optional[str] = None


class ProjectFeatureUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    business_purpose: Optional[str] = None
    request_type: Optional[str] = None
    expected_functionality: Optional[str] = None
    priority: Optional[str] = None
    expected_date: Optional[date] = None
    acceptance_criteria: Optional[str] = None
    status: Optional[str] = None
    status_reason: Optional[str] = None
    quoted_paise: Optional[int] = None
    is_billable: Optional[bool] = None
    dev_hours: Optional[float] = None
    test_hours: Optional[float] = None
    complexity: Optional[str] = None
    dev_cost_paise: Optional[int] = None
    expense_paise: Optional[int] = None
    estimated_delivery_date: Optional[date] = None
    assignee_user_id: Optional[str] = None
    internal_notes: Optional[str] = None


class ProjectFeatureResponse(OrmBase):
    id: str
    project_id: str
    cr_number: Optional[str] = None
    title: str
    description: Optional[str] = None
    business_purpose: Optional[str] = None
    request_type: str = "new_feature"
    expected_functionality: Optional[str] = None
    priority: str = "medium"
    expected_date: Optional[date] = None
    acceptance_criteria: Optional[str] = None
    status: str
    quoted_paise: Optional[int] = None
    is_billable: bool = True
    submitted_by: Optional[str] = None
    dev_hours: Optional[float] = None
    test_hours: Optional[float] = None
    complexity: Optional[str] = None
    dev_cost_paise: Optional[int] = None
    expense_paise: Optional[int] = None
    estimated_delivery_date: Optional[date] = None
    assignee_user_id: Optional[str] = None
    internal_notes: Optional[str] = None
    cancel_reason: Optional[str] = None
    rejection_reason: Optional[str] = None
    client_accepted_at: Optional[datetime] = None
    project_name: Optional[str] = None
    client_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    docs: List[ProjectFeatureDocResponse] = []


# ----------------- Invoice Items & Invoices -----------------
class InvoiceItemBase(BaseModel):
    description: str
    quantity: float = 1.0
    unit_rate_paise: int
    # Optional per-line kind so one invoice can mix development + maintenance, etc.
    # Aliases accepted: development/dev → custom_dev, maintenance/mnt → amc
    line_kind: Optional[str] = None
    hsn_sac: Optional[str] = None
    unit: Optional[str] = None

class InvoiceItemResponse(InvoiceItemBase, OrmBase):
    id: str
    amount_paise: int
    line_kind: Optional[str] = None
    hsn_sac: Optional[str] = None
    unit: Optional[str] = None


class InvoicePaymentResponse(OrmBase):
    id: str
    amount_paise: int
    method: Optional[str] = None
    reference: Optional[str] = None
    paid_at: Optional[datetime] = None
    note: Optional[str] = None
    status: str = "verified"
    rejection_reason: Optional[str] = None
    recorded_by: Optional[str] = None
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    has_proof: bool = False


class InvoicePaymentCreate(BaseModel):
    amount_paise: int = Field(gt=0)
    method: Optional[str] = "bank_transfer"
    reference: Optional[str] = None
    note: Optional[str] = None
    paid_at: Optional[datetime] = None
    # Founder may record as already verified (manual entry)
    mark_verified: bool = True


class InvoicePaymentReject(BaseModel):
    reason: str = Field(min_length=1)


class InvoiceCreate(BaseModel):
    # custom_dev | amc | advance | balance | mixed | … — mixed auto-detected from line_kind
    invoice_type: str = "custom_dev"
    client_id: str
    project_id: str
    issue_date: date
    due_date: date
    tax_rate_percent: float = 0.0
    # None = follow Settings; True/False = override for this invoice only
    gst_enabled: Optional[bool] = None
    notes: Optional[str] = None
    upi_id_override: Optional[str] = None
    upi_qr_url: Optional[str] = None
    items: List[InvoiceItemBase]
    period_months: int = 1
    covers_from: Optional[date] = None
    covers_to: Optional[date] = None
    monthly_rate_paise: Optional[int] = None
    discount_paise: int = 0
    po_number: Optional[str] = None
    # When creating already-settled historical invoices
    status: Optional[str] = None
    paid_at: Optional[datetime] = None

class InvoiceUpdate(BaseModel):
    """Explicit allow-list for invoice PATCH (F-003).

    ``project_id`` / ``client_id`` are intentionally absent — changing them on an
    existing invoice is rejected (422 via extra=forbid, or 400 in the router).
    """

    model_config = ConfigDict(extra="forbid")

    invoice_type: Optional[str] = None
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    tax_rate_percent: Optional[float] = None
    gst_enabled: Optional[bool] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    upi_id_override: Optional[str] = None
    upi_qr_url: Optional[str] = None
    items: Optional[List[InvoiceItemBase]] = None
    period_months: Optional[int] = None
    covers_from: Optional[date] = None
    covers_to: Optional[date] = None
    monthly_rate_paise: Optional[int] = None
    discount_paise: Optional[int] = None
    po_number: Optional[str] = None

class CreditNoteCreate(BaseModel):
    reason: Optional[str] = None
    # If omitted, credit the full parent total (ex-GST lines rebuilt as a single credit line).
    amount_paise: Optional[int] = None


class DebitNoteCreate(BaseModel):
    reason: Optional[str] = None
    # Extra charge ex-GST. Required — debit notes must state the additional amount.
    amount_paise: int
    description: Optional[str] = None


class GstRegistrationResponse(OrmBase):
    id: str
    gstin: str
    legal_name: Optional[str] = None
    address: Optional[str] = None
    state: Optional[str] = None
    state_code: str
    effective_from: date
    effective_to: Optional[date] = None
    is_primary: bool = False
    is_active: bool = True


class GstRegistrationCreate(BaseModel):
    gstin: str
    legal_name: Optional[str] = None
    address: Optional[str] = None
    state: Optional[str] = None
    state_code: str
    effective_from: date
    effective_to: Optional[date] = None
    is_primary: bool = False
    is_active: bool = True


class GstRegistrationUpdate(BaseModel):
    gstin: Optional[str] = None
    legal_name: Optional[str] = None
    address: Optional[str] = None
    state: Optional[str] = None
    state_code: Optional[str] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_primary: Optional[bool] = None
    is_active: Optional[bool] = None


class InvoiceResponse(OrmBase):
    id: str
    invoice_number: str
    invoice_type: str  # custom_dev | amc | mixed | late_fee | credit_note | …
    ledger: str = "development"  # development | maintenance | mixed
    client_id: str
    client_name: Optional[str] = None
    # Bill-to snapshot fields for document preview (from linked client)
    client_attn: Optional[str] = None
    client_designation: Optional[str] = None
    client_email: Optional[str] = None
    client_phone: Optional[str] = None
    client_address: Optional[str] = None
    client_city: Optional[str] = None
    client_state: Optional[str] = None
    client_pincode: Optional[str] = None
    client_gstin: Optional[str] = None
    client_pan: Optional[str] = None
    client_state_code: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    project_ref: Optional[str] = None
    status: str  # "draft", "sent", "paid", "overdue", "cancelled"
    issue_date: date
    due_date: date
    subtotal_paise: int
    tax_rate_percent: float
    tax_paise: int
    total_paise: int
    place_of_supply: Optional[str] = None
    supply_type: Optional[str] = None
    cgst_paise: int = 0
    sgst_paise: int = 0
    igst_paise: int = 0
    discount_paise: int = 0
    round_off_paise: int = 0
    amount_paid_paise: int = 0
    balance_due_paise: int = 0
    po_number: Optional[str] = None
    amount_in_words: Optional[str] = None
    notes: Optional[str] = None
    resolved_terms: List[str] = []
    snapshot_frozen_at: Optional[datetime] = None
    document_locked: bool = False
    payments: List[InvoicePaymentResponse] = []
    upi_id_override: Optional[str] = None
    upi_qr_url: Optional[str] = None
    paid_at: Optional[datetime] = None
    period_key: Optional[str] = None
    parent_invoice_id: Optional[str] = None
    parent_invoice_number: Optional[str] = None
    reminder_count: int = 0
    last_reminder_at: Optional[datetime] = None
    overdue_notified_at: Optional[datetime] = None
    email_status: str = "pending"  # "pending", "sent", "failed"
    email_error: Optional[str] = None
    email_retry_count: int = 0
    last_emailed_at: Optional[datetime] = None
    period_months: int = 1
    covers_from: Optional[date] = None
    covers_to: Optional[date] = None
    monthly_rate_paise: Optional[int] = None
    # Payment proof (client upload → founder verify)
    payment_proof_filename: Optional[str] = None
    payment_proof_content_type: Optional[str] = None
    payment_proof_uploaded_at: Optional[datetime] = None
    payment_proof_note: Optional[str] = None
    has_payment_proof: bool = False
    payment_verified_at: Optional[datetime] = None
    created_at: datetime
    items: List[InvoiceItemResponse] = []
    # Resolved payment hints for clients (company default or override)
    payment_upi_id: Optional[str] = None
    payment_upi_qr_url: Optional[str] = None
    payment_bank_beneficiary_name: Optional[str] = None
    payment_bank_name: Optional[str] = None
    payment_bank_account_number: Optional[str] = None
    payment_bank_ifsc: Optional[str] = None
    payment_paypal_id: Optional[str] = None
    payment_bank_upi_details: Optional[str] = None
    payment_bank_wire_instructions: Optional[str] = None
    payment_wire_lines: List[str] = []
    # Effective GST for this document (override or Settings).
    gst_enabled: bool = False
    # Raw per-invoice override: null = follow Settings; true/false = fixed for this invoice.
    gst_enabled_override: Optional[bool] = None
    # Unpaid + latest in series + no children — UI may allow GST series switch.
    can_change_gst: bool = False
    # Lowest free number if GST were toggled to the opposite series (preview only).
    gst_toggle_preview_number: Optional[str] = None
    # Unpaid/cancellable + tip of series — UI may show delete.
    can_delete: bool = False
    # Company document header snapshot (from settings)
    company_name: Optional[str] = None
    company_address: Optional[str] = None
    company_gstin: Optional[str] = None
    company_email: Optional[str] = None
    company_phone: Optional[str] = None
    company_website: Optional[str] = None
    company_tagline: Optional[str] = None
    company_logo_url: Optional[str] = None
    company_pan: Optional[str] = None
    company_state: Optional[str] = None
    company_state_code: Optional[str] = None
    gst_unregistered: bool = False
    default_sac_code: Optional[str] = None
    payment_terms: Optional[str] = None

# ----------------- Recurring Invoices -----------------
class RecurringInvoiceCreate(BaseModel):
    """Maintenance plan create — project_id is required."""
    client_id: str
    project_id: str
    amount_paise: int = 0
    send_day: int = Field(ge=1, le=28, default=1)
    due_after_days: int = Field(ge=1, default=5)
    reminder_every_days: int = Field(ge=1, default=2)
    starts_on: date = Field(default_factory=date.today)
    ends_on: Optional[date] = None
    line_items_template: List[dict] = []
    tax_rate_percent: float = 0.0
    day_of_month: int = Field(ge=1, le=28, default=1)
    next_run_date: date
    active: bool = True
    notes: Optional[str] = None


# Domain alias used in docs / newer callers
MaintenancePlanCreate = RecurringInvoiceCreate

class RecurringInvoiceUpdate(BaseModel):
    """Allow-list for plan PATCH. project_id/client_id cannot be reassigned here."""

    model_config = ConfigDict(extra="forbid")

    amount_paise: Optional[int] = None
    send_day: Optional[int] = Field(None, ge=1, le=28)
    due_after_days: Optional[int] = Field(None, ge=1)
    reminder_every_days: Optional[int] = Field(None, ge=1)
    starts_on: Optional[date] = None
    ends_on: Optional[date] = None
    line_items_template: Optional[List[dict]] = None
    tax_rate_percent: Optional[float] = None
    day_of_month: Optional[int] = Field(None, ge=1, le=28)
    next_run_date: Optional[date] = None
    active: Optional[bool] = None
    notes: Optional[str] = None

class RecurringInvoiceResponse(OrmBase):
    id: str
    client_id: str
    client_name: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    amount_paise: int = 0
    send_day: int = 1
    due_after_days: int = 5
    reminder_every_days: int = 2
    starts_on: date
    ends_on: Optional[date] = None
    line_items_template: List[dict] = []
    tax_rate_percent: float
    day_of_month: int
    next_run_date: date
    active: bool
    notes: Optional[str] = None
    created_at: datetime

# ----------------- Late Fee Actions -----------------
class LateFeeActionResponse(OrmBase):
    id: str
    invoice_id: str
    invoice_number: Optional[str] = None
    client_name: Optional[str] = None
    fee_paise: int
    fee_type: str  # "fixed", "percent"
    fee_value: float
    reason: str
    send_on: date
    repeat_every_days: Optional[int] = None
    reminder_every_hours: Optional[int] = None
    next_reminder_at: Optional[datetime] = None
    repeat_count: int = 0
    status: str  # "pending", "scheduled", "sent", "waived", "extended"
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    late_fee_invoice_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class LateFeeDecisionRequest(BaseModel):
    action: str = Field(description="'send_now', 'schedule', 'waive', or 'extend'")
    fee_type: str = Field(default="fixed", description="'fixed' or 'percent'")
    fee_value: float = Field(default=0.0)
    reason: str = Field(default="Payment not received by due date")
    send_on: Optional[date] = None
    repeat_every_days: Optional[int] = Field(default=None, le=3)
    extend_days: Optional[int] = Field(default=None, ge=1, le=90)
    reminder_every_hours: Optional[int] = Field(
        default=None, ge=1, le=168, description="Hours between overdue unpaid reminder emails"
    )

# ----------------- Notes -----------------
class NoteCreate(BaseModel):
    entity_type: str  # "client" or "lead"
    entity_id: str
    body: str

class NoteResponse(OrmBase):
    id: str
    entity_type: str
    entity_id: str
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    body: str
    created_at: datetime

# ----------------- Settings & Email Templates -----------------
class SettingsResponse(OrmBase):
    company_name: str
    company_address: Optional[str] = None
    company_gstin: Optional[str] = None
    company_logo_url: Optional[str] = None
    company_logo_content_hash: Optional[str] = None
    company_phone: Optional[str] = None
    company_website: Optional[str] = None
    company_tagline: Optional[str] = None
    company_pan: Optional[str] = None
    company_state: Optional[str] = None
    company_state_code: Optional[str] = None
    invoice_prefix: str
    invoice_next_number: int
    invoice_sequences: dict = {}
    invoice_prefix_scheme: List[dict] = []
    default_tax_rate: float
    gst_enabled: bool = False
    gst_unregistered: bool = False
    invoice_terms_by_type: dict = {}
    default_sac_code: Optional[str] = None
    pf_enabled: bool = False
    pf_employee_percent: float = 0
    esi_enabled: bool = False
    esi_employee_percent: float = 0
    pt_enabled: bool = False
    pt_paise: int = 0
    payment_terms: Optional[str] = None
    bank_beneficiary_name: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_upi_id: Optional[str] = None
    bank_paypal_id: Optional[str] = None
    bank_upi_details: Optional[str] = None
    bank_wire_instructions: Optional[str] = None
    bank_upi_qr_url: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int
    smtp_username: Optional[str] = None
    smtp_has_password: bool = False
    smtp_use_tls: bool
    smtp_from_name: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_auto_send_enabled: bool = True
    invoice_cc_email: Optional[str] = None
    gst_registrations: List[GstRegistrationResponse] = []

class SettingsUpdate(BaseModel):
    company_name: Optional[str] = None
    company_address: Optional[str] = None
    company_gstin: Optional[str] = None
    company_logo_url: Optional[str] = None
    company_phone: Optional[str] = None
    company_website: Optional[str] = None
    company_tagline: Optional[str] = None
    company_pan: Optional[str] = None
    company_state: Optional[str] = None
    company_state_code: Optional[str] = None
    invoice_prefix: Optional[str] = None
    invoice_next_number: Optional[int] = None
    invoice_sequences: Optional[dict] = None
    default_tax_rate: Optional[float] = None
    gst_enabled: Optional[bool] = None
    gst_unregistered: Optional[bool] = None
    invoice_terms_by_type: Optional[dict] = None
    default_sac_code: Optional[str] = None
    pf_enabled: Optional[bool] = None
    pf_employee_percent: Optional[float] = None
    esi_enabled: Optional[bool] = None
    esi_employee_percent: Optional[float] = None
    pt_enabled: Optional[bool] = None
    pt_paise: Optional[int] = None
    payment_terms: Optional[str] = None
    bank_beneficiary_name: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_upi_id: Optional[str] = None
    bank_paypal_id: Optional[str] = None
    bank_upi_details: Optional[str] = None
    bank_wire_instructions: Optional[str] = None
    bank_upi_qr_url: Optional[str] = None
    invoice_cc_email: Optional[str] = None

class SMTPSettingsUpdate(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: Optional[bool] = None
    smtp_from_name: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_auto_send_enabled: Optional[bool] = None

class TestEmailRequest(BaseModel):
    to_email: EmailStr

class EmailTemplateResponse(OrmBase):
    id: str
    subject: str
    body: str

class EmailTemplateUpdate(BaseModel):
    subject: str
    body: str

# ----------------- Engagements / Pipeline -----------------
class EngagementChecklistItemResponse(OrmBase):
    id: str
    engagement_id: str
    stage_number: int
    item_key: str
    label: str
    sort_order: int
    is_done: bool
    done_at: Optional[datetime] = None
    done_by: Optional[str] = None


class EngagementDocumentResponse(OrmBase):
    id: str
    engagement_id: str
    title: str
    original_filename: str
    content_type: str
    size_bytes: int
    doc_kind: str
    uploaded_by: Optional[str] = None
    created_at: datetime


class EngagementCreate(BaseModel):
    title: str
    company_name: str
    contact_name: str = ""
    designation: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    gstin: Optional[str] = None
    enquiry_source: Optional[str] = None
    engagement_kind: str = "project"  # project | amc_only
    notes: Optional[str] = None
    estimated_budget_paise: Optional[int] = None
    client_id: Optional[str] = None
    owner_user_id: Optional[str] = None


class EngagementUpdate(BaseModel):
    title: Optional[str] = None
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    designation: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    gstin: Optional[str] = None
    enquiry_source: Optional[str] = None
    engagement_kind: Optional[str] = None
    notes: Optional[str] = None
    estimated_budget_paise: Optional[int] = None
    quoted_paise: Optional[int] = None
    advance_paise: Optional[int] = None
    warranty_ends_on: Optional[date] = None
    owner_user_id: Optional[str] = None
    client_id: Optional[str] = None


class EngagementMarkLostRequest(BaseModel):
    lost_reason: str
    lost_note: Optional[str] = None


class EngagementJumpRequest(BaseModel):
    target_stage: int = Field(ge=1, le=11)


class EngagementLinkClientRequest(BaseModel):
    client_id: Optional[str] = None
    create_client: bool = False


class EngagementEnsureProjectRequest(BaseModel):
    """Stage 05 confirmed commercial terms — not pipeline estimates."""
    project_name: Optional[str] = None
    worker_ids: List[str] = []
    budget_paise: Optional[int] = Field(None, ge=0, description="Confirmed total development")
    development_days: Optional[int] = Field(None, ge=1)
    advance_paise: Optional[int] = Field(None, ge=0, description="Confirmed advance (0 allowed)")
    maintenance_amount_paise: Optional[int] = Field(None, ge=0)
    maintenance_send_day: int = Field(default=1, ge=1, le=28)
    maintenance_due_after_days: int = Field(default=5, ge=1)


class EngagementLinkInvoiceRequest(BaseModel):
    invoice_id: str
    role: str = Field(description="'advance' or 'final'")


class EngagementMaintenanceEnableRequest(BaseModel):
    amount_paise: int = Field(gt=0)
    send_day: int = Field(default=1, ge=1, le=28)
    due_after_days: int = Field(default=4, ge=1)  # generate 1st → due 5th
    reminder_every_days: int = Field(default=2, ge=1)
    description: str = "Monthly maintenance & support retainer"
    tax_rate_percent: float = 0.0
    jump_to_amc_stage: bool = True  # False = configure only (Stage 04), stay on current stage


class EngagementAdvanceInvoiceResponse(BaseModel):
    invoice_id: str
    invoice_number: str
    total_paise: int
    status: str
    engagement: Any  # EngagementDetailResponse — filled after create


class EngagementMaintenanceUpdateRequest(BaseModel):
    amount_paise: Optional[int] = Field(None, gt=0)
    send_day: Optional[int] = Field(None, ge=1, le=28)
    due_after_days: Optional[int] = Field(None, ge=1)
    reminder_every_days: Optional[int] = Field(None, ge=1)
    active: Optional[bool] = None
    notes: Optional[str] = None


class PipelineStageCount(BaseModel):
    stage: int
    name: str
    count: int


class PipelineSummaryResponse(BaseModel):
    stages: List[PipelineStageCount]
    open_value_paise: int = 0
    lost_this_month: int = 0
    maintenance_active: int = 0


class EngagementListItem(OrmBase):
    id: str
    title: str
    company_name: str
    contact_name: str
    enquiry_source: Optional[str] = None
    engagement_kind: str
    current_stage: int
    stage_name: Optional[str] = None
    client_stage_label: Optional[str] = None
    status: str
    estimated_budget_paise: Optional[int] = None
    quoted_paise: Optional[int] = None
    advance_paise: Optional[int] = None
    pipeline_value_paise: Optional[int] = None
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    warranty_ends_on: Optional[date] = None
    amc_active: bool = False
    checklist_progress_percent: int = 0
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class EngagementDetailResponse(EngagementListItem):
    email: Optional[str] = None
    phone: Optional[str] = None
    designation: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    gstin: Optional[str] = None
    notes: Optional[str] = None
    lost_reason: Optional[str] = None
    lost_note: Optional[str] = None
    advance_invoice_id: Optional[str] = None
    final_invoice_id: Optional[str] = None
    recurring_invoice_id: Optional[str] = None
    owner_user_id: Optional[str] = None
    owner_name: Optional[str] = None
    client_message: Optional[str] = None
    checklist_items: List[EngagementChecklistItemResponse] = []
    documents: List[EngagementDocumentResponse] = []
    recurring: Optional[RecurringInvoiceResponse] = None
    recent_maintenance_invoices: List["InvoiceResponse"] = []


# ----------------- Dashboard -----------------
class RecentItem(BaseModel):
    id: str
    type: str  # "project", "lead", "invoice"
    title: str
    subtitle: Optional[str] = None
    status: str
    date: datetime

class RevenueMonthPoint(BaseModel):
    month: str  # short label e.g. "Jun"
    year_month: str  # "YYYY-MM"
    billed_paise: int = 0
    collected_paise: int = 0


class DashboardResponse(BaseModel):
    running_projects_count: int
    open_leads_count: int
    unpaid_invoices_total_paise: int
    overdue_invoices_count: int
    failed_email_invoices_count: int = 0
    recent_items: List[RecentItem] = []
    needs_action_invoices: List[LateFeeActionResponse] = []
    revenue_by_month: List[RevenueMonthPoint] = []


# ----------------- Staff / Worker management -----------------

class StaffProfileResponse(OrmBase):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    department: Optional[str] = None
    job_title: Optional[str] = None
    reports_to_user_id: Optional[str] = None
    reports_to_name: Optional[str] = None
    joined_on: Optional[date] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    bank_account: Optional[str] = None
    bank_ifsc: Optional[str] = None
    pending_bank_account: Optional[str] = None
    pending_bank_ifsc: Optional[str] = None
    monthly_gross_paise: Optional[int] = None
    hourly_rate_paise: Optional[int] = None
    casual_leave_days: int = 12
    sick_leave_days: int = 6
    leave_balance: Optional["LeaveBalanceResponse"] = None
    created_at: Optional[datetime] = None


class StaffProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = None
    reports_to_user_id: Optional[str] = None
    joined_on: Optional[date] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    bank_account: Optional[str] = None
    bank_ifsc: Optional[str] = None
    monthly_gross_paise: Optional[int] = None
    hourly_rate_paise: Optional[int] = None
    casual_leave_days: Optional[int] = None
    sick_leave_days: Optional[int] = None


class LeaveBalanceResponse(BaseModel):
    casual_allowance: int
    casual_used: float
    casual_remaining: float
    sick_allowance: int
    sick_used: float
    sick_remaining: float
    unpaid_used: float
    # Hour accounting (1 day = 8h, half day = 4h)
    casual_allowance_hours: float = 0
    casual_used_hours: float = 0
    casual_remaining_hours: float = 0
    sick_allowance_hours: float = 0
    sick_used_hours: float = 0
    sick_remaining_hours: float = 0
    unpaid_used_hours: float = 0


class WorkTaskCreate(BaseModel):
    work_date: date
    title: str = Field(min_length=1, max_length=255)
    project_id: Optional[str] = None
    assignee_user_id: Optional[str] = None
    status: str = "todo"


class WorkTaskUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    project_id: Optional[str] = None


class WorkTaskResponse(OrmBase):
    id: str
    work_date: date
    title: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    assignee_user_id: str
    assignee_name: Optional[str] = None
    created_by_user_id: Optional[str] = None
    status: str
    created_at: datetime


class AttendanceResponse(OrmBase):
    id: str
    user_id: str
    user_name: Optional[str] = None
    work_date: date
    check_in_at: Optional[datetime] = None
    check_out_at: Optional[datetime] = None
    correction_reason: Optional[str] = None
    correction_note: Optional[str] = None
    correction_status: Optional[str] = None


class AttendanceCorrectionRequest(BaseModel):
    work_date: date
    reason: str  # missed / wfh / on_duty
    note: Optional[str] = None
    check_in_at: Optional[datetime] = None
    check_out_at: Optional[datetime] = None


class HolidayCreate(BaseModel):
    holiday_date: date
    name: str = Field(min_length=1, max_length=255)


class HolidayResponse(OrmBase):
    id: str
    holiday_date: date
    name: str


class LeaveRequestCreate(BaseModel):
    leave_type: str  # casual / sick / unpaid
    start_date: date
    end_date: date
    half_day: bool = False
    reason: Optional[str] = None


class LeaveRequestResponse(OrmBase):
    id: str
    user_id: str
    user_name: Optional[str] = None
    leave_type: str
    start_date: date
    end_date: date
    half_day: bool
    reason: Optional[str] = None
    status: str
    decided_by_user_id: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime


class ExpenseClaimCreate(BaseModel):
    expense_date: date
    category: str  # travel / food / other
    amount_paise: int = Field(gt=0)
    note: Optional[str] = None


class ExpenseClaimResponse(OrmBase):
    id: str
    user_id: str
    user_name: Optional[str] = None
    expense_date: date
    category: str
    amount_paise: int
    note: Optional[str] = None
    receipt_object_key: Optional[str] = None
    receipt_original_filename: Optional[str] = None
    status: str
    decided_by_user_id: Optional[str] = None
    decided_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    created_at: datetime


class StaffDocumentResponse(OrmBase):
    id: str
    user_id: str
    title: str
    kind: str
    original_filename: str
    content_type: str
    size_bytes: int
    uploaded_by: Optional[str] = None
    created_at: datetime


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)


class AnnouncementResponse(OrmBase):
    id: str
    title: str
    body: str
    created_by_user_id: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: datetime


class PayslipPublishRequest(BaseModel):
    user_id: str
    month_key: str = Field(pattern=r"^\d{4}-\d{2}$")
    deductions_paise: int = 0
    note: Optional[str] = None
    gross_paise: Optional[int] = None  # defaults to monthly_gross_paise


class PayslipResponse(OrmBase):
    id: str
    user_id: str
    user_name: Optional[str] = None
    month_key: str
    gross_paise: int
    deductions_paise: int
    net_paise: int
    note: Optional[str] = None
    created_at: datetime


class StaffHomeResponse(BaseModel):
    profile: StaffProfileResponse
    today_tasks: List[WorkTaskResponse] = []
    today_attendance: Optional[AttendanceResponse] = None
    projects: List[dict] = []
    leave_balance: LeaveBalanceResponse
    open_expenses: List[ExpenseClaimResponse] = []
    latest_announcement: Optional[AnnouncementResponse] = None
    latest_payslip: Optional[PayslipResponse] = None


class StaffInboxResponse(BaseModel):
    pending_leave: List[LeaveRequestResponse] = []
    pending_corrections: List[AttendanceResponse] = []
    pending_expenses: List[ExpenseClaimResponse] = []
    pending_bank_changes: List[StaffProfileResponse] = []


# ----------------- Company expenses (studio ledger) -----------------


class CompanyExpenseResponse(OrmBase):
    id: str
    expense_date: date
    section: str  # recurring / daily / upgrade / other
    category: str
    title: str
    vendor: Optional[str] = None
    amount_paise: int
    note: Optional[str] = None
    receipt_object_key: Optional[str] = None
    receipt_original_filename: Optional[str] = None
    renewal_interval_months: Optional[int] = None  # 1 / 3 / 6 / 12
    next_renewal_on: Optional[date] = None
    due_status: Optional[str] = None  # overdue / due_soon / upcoming
    created_by_user_id: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CompanyExpenseSectionTotal(BaseModel):
    section: str
    total_paise: int


class CompanyExpenseListResponse(BaseModel):
    items: List[CompanyExpenseResponse] = []
    section_totals: List[CompanyExpenseSectionTotal] = []
    month_total_paise: int = 0
    largest: Optional[CompanyExpenseResponse] = None
    upcoming_renewals: List[CompanyExpenseResponse] = []

