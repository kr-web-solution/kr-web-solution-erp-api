"""KR Workspace engagement pipeline stage catalog (01–14).

Sales board: 01–05. Project Delivery: 05–14 (SDLC).
"""

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
    deliverable: str
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
        "deliverable": "Enquiry logged",
        "checklist": [],
    },
    {
        "number": 2,
        "key": "discovery",
        "name": "Discovery & Requirements",
        "client_label": "Understanding your needs",
        "client_message": "We are gathering requirements, budget, and scope.",
        "deliverable": "Discovery notes",
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
        "deliverable": "Approved quotation",
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
        "deliverable": "Signed contract",
        "checklist": [
            {"key": "sign_agreement", "label": "Sign agreement"},
            {"key": "raise_invoice", "label": "Raise invoice"},
            {"key": "confirm_payment", "label": "Confirm payment"},
        ],
    },
    # ---------- Project delivery (SDLC) ----------
    {
        "number": 5,
        "key": "requirements",
        "name": "Requirement Analysis",
        "client_label": "Gathering requirements",
        "client_message": "We are documenting what you need so the solution matches your goals.",
        "deliverable": "SRS Document",
        "checklist": [
            {"key": "client_requirements", "label": "Client requirements"},
            {"key": "user_roles_permissions", "label": "User roles and permissions"},
            {"key": "functional_requirements", "label": "Functional requirements"},
            {"key": "non_functional_requirements", "label": "Non-functional requirements"},
            {"key": "project_scope_limitations", "label": "Project scope and limitations"},
        ],
    },
    {
        "number": 6,
        "key": "feasibility",
        "name": "Feasibility Study & Planning",
        "client_label": "Planning the project",
        "client_message": "We are confirming budget, technology, and timeline before build starts.",
        "deliverable": "Project Plan",
        "checklist": [
            {"key": "budget_estimation", "label": "Budget estimation"},
            {"key": "technology_selection", "label": "Technology selection"},
            {"key": "server_requirements", "label": "Server requirements"},
            {"key": "timeline_estimation", "label": "Timeline estimation"},
            {"key": "risk_assessment", "label": "Risk assessment"},
        ],
    },
    {
        "number": 7,
        "key": "architecture",
        "name": "System Architecture & Database Design",
        "client_label": "Designing the system",
        "client_message": "We are designing the architecture, database, and integrations.",
        "deliverable": "Architecture Document",
        "checklist": [
            {"key": "system_architecture", "label": "System architecture"},
            {"key": "database_er", "label": "Database ER diagrams"},
            {"key": "api_architecture", "label": "API architecture"},
            {"key": "auth_architecture", "label": "Authentication architecture"},
            {"key": "third_party_integrations", "label": "Third-party integrations"},
        ],
    },
    {
        "number": 8,
        "key": "uiux",
        "name": "UI/UX Design",
        "client_label": "Designing the experience",
        "client_message": "We are designing screens, flows, and the look of your product.",
        "deliverable": "Approved UI Design",
        "checklist": [
            {"key": "wireframes", "label": "Wireframes"},
            {"key": "user_flows", "label": "User flows"},
            {"key": "design_system", "label": "Design system"},
            {"key": "responsive_layouts", "label": "Responsive layouts"},
            {"key": "figma_prototypes", "label": "Figma prototypes"},
        ],
    },
    {
        "number": 9,
        "key": "environment",
        "name": "Environment & Infrastructure Setup",
        "client_label": "Setting up environments",
        "client_message": "We are preparing development, staging, and production environments.",
        "deliverable": "Development Environment",
        "checklist": [
            {"key": "github_repos", "label": "GitHub repositories"},
            {"key": "dev_environment", "label": "Development environment"},
            {"key": "docker_config", "label": "Docker configuration"},
            {"key": "database_setup", "label": "Database setup"},
            {"key": "cicd_pipeline", "label": "CI/CD pipeline"},
            {"key": "env_tiers", "label": "Dev, staging and production environments"},
        ],
    },
    {
        "number": 10,
        "key": "development",
        "name": "Development / Implementation",
        "client_label": "Building your product",
        "client_message": "Our team is building the application and integrating services.",
        "deliverable": "Working Application",
        "checklist": [
            {"key": "backend_development", "label": "Backend development"},
            {"key": "frontend_development", "label": "Frontend development"},
            {"key": "database_integration", "label": "Database integration"},
            {"key": "api_integration", "label": "API integration"},
            {"key": "auth_authorization", "label": "Authentication and authorization"},
            {"key": "third_party_services", "label": "Third-party services"},
        ],
    },
    {
        "number": 11,
        "key": "testing",
        "name": "Testing & Quality Assurance",
        "client_label": "Testing quality",
        "client_message": "We are testing the product thoroughly before your acceptance review.",
        "deliverable": "Tested Application",
        "checklist": [
            {"key": "unit_testing", "label": "Unit testing"},
            {"key": "integration_testing", "label": "Integration testing"},
            {"key": "api_testing", "label": "API testing"},
            {"key": "ui_testing", "label": "UI testing"},
            {"key": "security_testing", "label": "Security testing"},
            {"key": "performance_testing", "label": "Performance testing"},
            {"key": "uat", "label": "User acceptance testing (UAT)"},
        ],
    },
    {
        "number": 12,
        "key": "deployment",
        "name": "Deployment & Release",
        "client_label": "Going live",
        "client_message": "We are publishing your application to production.",
        "deliverable": "Live Application",
        "checklist": [
            {"key": "production_server", "label": "Production server setup"},
            {"key": "domain_ssl", "label": "Domain and SSL configuration"},
            {"key": "database_migration", "label": "Database migration"},
            {"key": "cicd_deployment", "label": "CI/CD deployment"},
            {"key": "backup_config", "label": "Backup configuration"},
            {"key": "production_verification", "label": "Production verification"},
        ],
    },
    {
        "number": 13,
        "key": "handover",
        "name": "Documentation & Handover",
        "client_label": "Handover & docs",
        "client_message": "We are preparing documentation, training, and handover materials.",
        "deliverable": "Handover Package",
        "checklist": [
            {"key": "user_documentation", "label": "User documentation"},
            {"key": "admin_documentation", "label": "Admin documentation"},
            {"key": "api_documentation", "label": "API documentation"},
            {"key": "server_documentation", "label": "Server documentation"},
            {"key": "source_code_handover", "label": "Source code handover"},
            {"key": "training", "label": "Training"},
        ],
    },
    {
        "number": 14,
        "key": "maintenance",
        "name": "Maintenance & Continuous Improvement",
        "client_label": "Ongoing support",
        "client_message": "Your product is live and under continuous maintenance and improvement.",
        "deliverable": "Maintained Product",
        "checklist": [
            {"key": "bug_fixes", "label": "Bug fixes"},
            {"key": "security_updates", "label": "Security updates"},
            {"key": "server_monitoring", "label": "Server monitoring"},
            {"key": "performance_optimization", "label": "Performance optimization"},
            {"key": "feature_enhancements", "label": "Feature enhancements"},
            {"key": "regular_backups", "label": "Regular backups"},
        ],
    },
]

STAGES_BY_NUMBER: Dict[int, StageDef] = {s["number"]: s for s in PIPELINE_STAGES}
MAX_STAGE = 14
MIN_STAGE = 1
DELIVERY_STAGES = set(range(5, 15))  # 05–14
WARRANTY_DEFAULT_DAYS = 90

# Special delivery milestones
HANDOVER_PHASE = 13  # Documentation & Handover — close without AMC allowed
MAINTENANCE_PHASE = 14  # Maintenance & Continuous Improvement


def build_checklist_seed() -> List[tuple[int, str, str, int]]:
    """Return (stage_number, item_key, label, sort_order) for stages 02–14."""
    rows: List[tuple[int, str, str, int]] = []
    for stage in PIPELINE_STAGES:
        if stage["number"] < 2:
            continue
        for idx, item in enumerate(stage["checklist"]):
            rows.append((stage["number"], item["key"], item["label"], idx))
    return rows


def build_project_delivery_checklist_seed() -> List[tuple[int, str, str, int]]:
    """Return (stage_number, item_key, label, sort_order) for project delivery stages 05–14."""
    rows: List[tuple[int, str, str, int]] = []
    for stage in PIPELINE_STAGES:
        if stage["number"] < 5:
            continue
        for idx, item in enumerate(stage["checklist"]):
            rows.append((stage["number"], item["key"], item["label"], idx))
    return rows


# Sales pipeline board shows 01–05; 06–14 live on Projects
SALES_MAX_STAGE = 5
DELIVERY_MIN_STAGE = 5
DELIVERY_MAX_STAGE = 14
