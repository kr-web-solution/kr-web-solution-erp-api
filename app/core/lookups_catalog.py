"""Seed catalogs for all expandable lookup tables."""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(prefix: str, code: str, name: str, sort: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"{prefix}_{code}"[:36],
        "code": code,
        "name": name,
        "description": extra.pop("description", None),
        "sort_order": sort,
        "is_system": True,
        "is_active": True,
        **extra,
    }


PARTY_TYPES = [
    _row("pty", "staff", "Staff", 10, description="Internal team member"),
    _row("pty", "client", "Client", 20, description="Verified client company contact"),
    _row("pty", "lead", "Lead", 30, description="Lead / prospect contact"),
]

# roles keep stable IDs from roles_catalog (rol_admin, …) — seeded separately

INVOICE_LEDGERS = [
    _row("ild", "development", "Development", 10),
    _row("ild", "maintenance", "Maintenance", 20),
]

INVOICE_TYPES = [
    _row("itp", "custom_dev", "Custom development", 10, service_code="DEV", default_ledger_code="development"),
    _row("itp", "project", "Project", 20, service_code="DEV", default_ledger_code="development"),
    _row("itp", "advance", "Advance", 30, service_code="DEV", default_ledger_code="development"),
    _row("itp", "balance", "Balance", 40, service_code="DEV", default_ledger_code="development"),
    _row("itp", "hosting", "Hosting", 50, service_code="HST", default_ledger_code="development"),
    _row("itp", "services", "Services", 60, service_code="SVC", default_ledger_code="development"),
    _row("itp", "licensing", "Licensing", 70, service_code="LIC", default_ledger_code="development"),
    _row("itp", "amc", "AMC", 80, service_code="MNT", default_ledger_code="maintenance"),
    _row("itp", "maintenance", "Maintenance", 90, service_code="MNT", default_ledger_code="maintenance"),
    _row("itp", "late_fee", "Late fee", 100, service_code="SVC", default_ledger_code="development"),
    _row("itp", "credit_note", "Credit note", 110, service_code="CRN", default_ledger_code="development"),
]

INVOICE_STATUSES = [
    _row("ist", "draft", "Draft", 10),
    _row("ist", "sent", "Sent", 20),
    _row("ist", "paid", "Paid", 30),
    _row("ist", "overdue", "Overdue", 40),
    _row("ist", "cancelled", "Cancelled", 50),
    _row("ist", "void", "Void", 60),
]

EMAIL_DELIVERY_STATUSES = [
    _row("eds", "pending", "Pending", 10),
    _row("eds", "sent", "Sent", 20),
    _row("eds", "failed", "Failed", 30),
]

PROJECT_TYPES = [
    _row("ptp", "website", "Website", 10),
    _row("ptp", "web_app", "Web application", 20),
    _row("ptp", "mobile_app", "Mobile app", 30),
    _row("ptp", "ecommerce", "E-commerce", 40),
    _row("ptp", "amc", "AMC / retainer", 50),
    _row("ptp", "custom", "Custom", 60),
]

PROJECT_STATUSES = [
    _row("pst", "lead", "Lead", 10),
    _row("pst", "running", "Running", 20),
    _row("pst", "on_hold", "On hold", 30),
    _row("pst", "stopped", "Stopped", 40),
    _row("pst", "completed", "Completed", 50),
]

PIPELINE_STAGES = [
    _row("pps", "enquiry", "Client Enquiry", 1, stage_number=1),
    _row("pps", "discovery", "Discovery & Requirements", 2, stage_number=2),
    _row("pps", "quotation", "Quotation & Approval", 3, stage_number=3),
    _row("pps", "contract", "Contract & Advance Payment", 4, stage_number=4),
    _row("pps", "onboarding", "Project Onboarding", 5, stage_number=5),
    _row("pps", "design", "Design", 6, stage_number=6),
    _row("pps", "development", "Development", 7, stage_number=7),
    _row("pps", "testing", "Testing & QA", 8, stage_number=8),
    _row("pps", "deployment", "Deployment", 9, stage_number=9),
    _row("pps", "training", "Training & Handover", 10, stage_number=10),
    _row("pps", "maintenance", "Maintenance", 11, stage_number=11),
]

ENGAGEMENT_KINDS = [
    _row("egk", "project", "Project", 10),
    _row("egk", "amc_only", "AMC only", 20),
]

ENGAGEMENT_STATUSES = [
    _row("egs", "active", "Active", 10),
    _row("egs", "on_hold", "On hold", 20),
    _row("egs", "lost", "Lost", 30),
    _row("egs", "maintenance", "Maintenance", 40),
    _row("egs", "completed", "Completed", 50),
]

ENQUIRY_SOURCES = [
    _row("eqs", "instagram_ads", "Instagram Ads", 10),
    _row("eqs", "cold_call", "Cold Call", 20),
    _row("eqs", "website", "Website", 30),
    _row("eqs", "whatsapp", "WhatsApp", 40),
    _row("eqs", "referral", "Referral", 50),
    _row("eqs", "email", "Email", 60),
]

LOST_REASONS = [
    _row("lsr", "price", "Price", 10),
    _row("lsr", "timing", "Timing", 20),
    _row("lsr", "competitor", "Competitor", 30),
    _row("lsr", "no_response", "No response", 40),
    _row("lsr", "other", "Other", 50),
]

LEAD_STATUSES = [
    _row("lds", "new", "New", 10),
    _row("lds", "contacted", "Contacted", 20),
    _row("lds", "proposal", "Proposal", 30),
    _row("lds", "won", "Won", 40),
    _row("lds", "lost", "Lost", 50),
]

LEAD_SOURCES = [
    _row("ldc", "website", "Website", 10),
    _row("ldc", "referral", "Referral", 20),
    _row("ldc", "cold_call", "Cold call", 30),
    _row("ldc", "whatsapp", "WhatsApp", 40),
    _row("ldc", "other", "Other", 50),
]

DOCUMENT_KINDS = [
    _row("dok", "quotation", "Quotation", 10, scope="project"),
    _row("dok", "agreement", "Agreement", 20, scope="project"),
    _row("dok", "other", "Other", 30, scope="project"),
    _row("dok", "enquiry", "Enquiry", 40, scope="engagement"),
    _row("dok", "brief", "Brief", 50, scope="engagement"),
    _row("dok", "quote", "Quote", 60, scope="engagement"),
    _row("dok", "offer", "Offer letter", 70, scope="staff"),
    _row("dok", "id", "ID proof", 80, scope="staff"),
    _row("dok", "note", "Note", 90, scope="feature"),
    _row("dok", "link", "Link", 100, scope="feature"),
    _row("dok", "spec", "Spec", 110, scope="feature"),
]

FEATURE_STATUSES = [
    _row("fst", "requested", "Requested", 10),
    _row("fst", "approved", "Approved", 20),
    _row("fst", "in_progress", "In progress", 30),
    _row("fst", "done", "Done", 40),
    _row("fst", "rejected", "Rejected", 50),
]

LEAVE_TYPES = [
    _row("lvt", "casual", "Casual leave", 10),
    _row("lvt", "sick", "Sick leave", 20),
    _row("lvt", "unpaid", "Unpaid leave", 30),
]

APPROVAL_STATUSES = [
    _row("aps", "pending", "Pending", 10),
    _row("aps", "approved", "Approved", 20),
    _row("aps", "rejected", "Rejected", 30),
    _row("aps", "paid", "Paid", 40),
]

WORK_TASK_STATUSES = [
    _row("wts", "todo", "To do", 10),
    _row("wts", "doing", "Doing", 20),
    _row("wts", "done", "Done", 30),
]

EXPENSE_SECTIONS = [
    _row("exs", "recurring", "Recurring", 10),
    _row("exs", "daily", "Daily", 20),
    _row("exs", "upgrade", "Upgrade", 30),
    _row("exs", "other", "Other", 40),
]

EXPENSE_CATEGORIES = [
    _row("exc", "server", "Server", 10, section_code="recurring"),
    _row("exc", "domain", "Domain", 20, section_code="recurring"),
    _row("exc", "hosting", "Hosting", 30, section_code="recurring"),
    _row("exc", "software", "Software", 40, section_code="recurring"),
    _row("exc", "email", "Email", 50, section_code="recurring"),
    _row("exc", "food", "Food", 60, section_code="daily"),
    _row("exc", "travel", "Travel", 70, section_code="daily"),
    _row("exc", "supplies", "Supplies", 80, section_code="daily"),
    _row("exc", "fuel", "Fuel", 90, section_code="daily"),
    _row("exc", "petty_cash", "Petty cash", 100, section_code="daily"),
    _row("exc", "furniture", "Furniture", 110, section_code="upgrade"),
    _row("exc", "equipment", "Equipment", 120, section_code="upgrade"),
    _row("exc", "investment", "Investment", 130, section_code="upgrade"),
    _row("exc", "other", "Other", 140, section_code="other"),
]

EXPENSE_CLAIM_CATEGORIES = [
    _row("ecc", "travel", "Travel", 10),
    _row("ecc", "food", "Food", 20),
    _row("ecc", "other", "Other", 30),
]

LATE_FEE_TYPES = [
    _row("lft", "fixed", "Fixed", 10),
    _row("lft", "percent", "Percent", 20),
]

LATE_FEE_STATUSES = [
    _row("lfs", "pending", "Pending", 10),
    _row("lfs", "scheduled", "Scheduled", 20),
    _row("lfs", "sent", "Sent", 30),
    _row("lfs", "waived", "Waived", 40),
    _row("lfs", "extended", "Extended", 50),
]

ATTENDANCE_CORRECTION_REASONS = [
    _row("acr", "missed", "Missed punch", 10),
    _row("acr", "wfh", "Work from home", 20),
    _row("acr", "on_duty", "On duty", 30),
]
