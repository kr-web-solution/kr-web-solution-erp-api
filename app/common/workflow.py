"""KR Workspace engagement pipeline stage catalog (01–11)."""

from typing import Dict, List, TypedDict


class ChecklistDef(TypedDict):
    key: str
    label: str


class StageDef(TypedDict):
    number: int
    key: str
    name: str
    client_label: str
    client_message: str
    checklist: List[ChecklistDef]


ENQUIRY_SOURCES = [
    ("instagram_ads", "Instagram Ads"),
    ("cold_call", "Cold Call"),
    ("website", "Website"),
    ("whatsapp", "WhatsApp"),
    ("referral", "Referral"),
    ("email", "Email"),
]

LOST_REASONS = [
    ("price", "Price"),
    ("timing", "Timing"),
    ("competitor", "Competitor"),
    ("no_response", "No response"),
    ("other", "Other"),
]

PIPELINE_STAGES: List[StageDef] = [
    {
        "number": 1,
        "key": "enquiry",
        "name": "Client Enquiry",
        "client_label": "Enquiry received",
        "client_message": "We have received your enquiry and will be in touch shortly.",
        "checklist": [],
    },
    {
        "number": 2,
        "key": "discovery",
        "name": "Discovery & Requirements",
        "client_label": "Understanding your needs",
        "client_message": "We are gathering requirements, budget, and scope.",
        "checklist": [
            {"key": "client_needs", "label": "Client needs"},
            {"key": "budget", "label": "Budget"},
            {"key": "scope", "label": "Scope"},
            {"key": "feasibility", "label": "Feasibility"},
        ],
    },
    {
        "number": 3,
        "key": "quotation",
        "name": "Quotation & Approval",
        "client_label": "Quotation in progress",
        "client_message": "We are preparing and finalising your commercial proposal.",
        "checklist": [
            {"key": "prepare_quotation", "label": "Prepare quotation"},
            {"key": "send", "label": "Send"},
            {"key": "negotiate", "label": "Negotiate"},
            {"key": "accept", "label": "Accept"},
        ],
    },
    {
        "number": 4,
        "key": "contract",
        "name": "Contract & Advance Payment",
        "client_label": "Contract & advance",
        "client_message": "Agreement and advance payment are being completed.",
        "checklist": [
            {"key": "sign_agreement", "label": "Sign agreement"},
            {"key": "raise_invoice", "label": "Raise invoice"},
            {"key": "confirm_payment", "label": "Confirm payment"},
        ],
    },
    {
        "number": 5,
        "key": "onboarding",
        "name": "Project Onboarding & Planning",
        "client_label": "Project kickoff",
        "client_message": "Your project is being set up with milestones and the delivery team.",
        "checklist": [
            {"key": "create_project", "label": "Create project"},
            {"key": "assign_team", "label": "Assign team"},
            {"key": "requirements_signoff", "label": "Requirements sign-off"},
            {"key": "milestones", "label": "Milestones"},
        ],
    },
    {
        "number": 6,
        "key": "design_dev",
        "name": "Design & Development",
        "client_label": "Design & development",
        "client_message": "We are building your product and reviewing progress regularly.",
        "checklist": [
            {"key": "uiux_approval", "label": "UI/UX approval"},
            {"key": "development", "label": "Development"},
            {"key": "progress_reviews", "label": "Progress reviews"},
        ],
    },
    {
        "number": 7,
        "key": "qa",
        "name": "Internal QA & Testing",
        "client_label": "Quality assurance",
        "client_message": "Quality assurance is in progress before your review.",
        "checklist": [
            {"key": "functional", "label": "Functional"},
            {"key": "security", "label": "Security"},
            {"key": "integration", "label": "Integration"},
            {"key": "regression", "label": "Regression"},
        ],
    },
    {
        "number": 8,
        "key": "uat",
        "name": "Client Review & UAT",
        "client_label": "Your review (UAT)",
        "client_message": "Waiting for your demo feedback and final acceptance.",
        "checklist": [
            {"key": "client_demo", "label": "Client demo"},
            {"key": "feedback", "label": "Feedback"},
            {"key": "fixes", "label": "Fixes"},
            {"key": "final_acceptance", "label": "Final acceptance"},
        ],
    },
    {
        "number": 9,
        "key": "deploy",
        "name": "Deployment & Handover",
        "client_label": "Going live",
        "client_message": "We are deploying to production and preparing handover.",
        "checklist": [
            {"key": "production_deployment", "label": "Production deployment"},
            {"key": "verification", "label": "Verification"},
            {"key": "training", "label": "Training"},
            {"key": "documentation", "label": "Documentation"},
        ],
    },
    {
        "number": 10,
        "key": "closure",
        "name": "Closure & Warranty",
        "client_label": "Project closure",
        "client_message": "Final billing and warranty period are being completed.",
        "checklist": [
            {"key": "final_invoice", "label": "Final invoice"},
            {"key": "payment", "label": "Payment"},
            {"key": "signoff", "label": "Sign-off"},
            {"key": "warranty_start", "label": "Warranty start"},
        ],
    },
    {
        "number": 11,
        "key": "maintenance",
        "name": "Maintenance (AMC)",
        "client_label": "Maintenance plan",
        "client_message": "Your paid maintenance / support retainer is active.",
        "checklist": [
            {"key": "enable_retainer", "label": "Enable retainer"},
            {"key": "set_amount_schedule", "label": "Set monthly amount / send day"},
            {"key": "confirm_first_period", "label": "Confirm first period"},
            {"key": "support_owner", "label": "Handover to support owner"},
        ],
    },
]

STAGES_BY_NUMBER: Dict[int, StageDef] = {s["number"]: s for s in PIPELINE_STAGES}
MAX_STAGE = 11
MIN_STAGE = 1
DELIVERY_STAGES = set(range(5, 12))  # 05–11
WARRANTY_DEFAULT_DAYS = 90


def build_checklist_seed() -> List[tuple[int, str, str, int]]:
    """Return (stage_number, item_key, label, sort_order) for stages 02–11."""
    rows: List[tuple[int, str, str, int]] = []
    for stage in PIPELINE_STAGES:
        if stage["number"] < 2:
            continue
        for idx, item in enumerate(stage["checklist"]):
            rows.append((stage["number"], item["key"], item["label"], idx))
    return rows


def build_project_delivery_checklist_seed() -> List[tuple[int, str, str, int]]:
    """Return (stage_number, item_key, label, sort_order) for project delivery stages 05–11."""
    rows: List[tuple[int, str, str, int]] = []
    for stage in PIPELINE_STAGES:
        if stage["number"] < 5:
            continue
        for idx, item in enumerate(stage["checklist"]):
            rows.append((stage["number"], item["key"], item["label"], idx))
    return rows


# Sales pipeline board shows 01–05; 06–11 live on Projects
SALES_MAX_STAGE = 5
DELIVERY_MIN_STAGE = 5
DELIVERY_MAX_STAGE = 11
