from enum import StrEnum

class UserRole(StrEnum):
    ADMIN = "admin"
    FOUNDER_CEO = "founder_ceo"
    WORKER = "worker"
    CLIENT = "client"

# Admin and Founder share full workspace access
PRIVILEGED_ROLES = (UserRole.ADMIN, UserRole.FOUNDER_CEO)

class ProjectStatus(StrEnum):
    LEAD = "lead"
    RUNNING = "running"
    ON_HOLD = "on_hold"
    STOPPED = "stopped"
    COMPLETED = "completed"

class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"

class InvoiceType(StrEnum):
    PROJECT = "project"
    MAINTENANCE = "maintenance"

class LeadStatus(StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"

class EngagementKind(StrEnum):
    PROJECT = "project"
    AMC_ONLY = "amc_only"

class EngagementStatus(StrEnum):
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    LOST = "lost"
    MAINTENANCE = "maintenance"
    COMPLETED = "completed"

class EnquirySource(StrEnum):
    INSTAGRAM_ADS = "instagram_ads"
    COLD_CALL = "cold_call"
    WEBSITE = "website"
    WHATSAPP = "whatsapp"
    REFERRAL = "referral"
    EMAIL = "email"

class LostReason(StrEnum):
    PRICE = "price"
    TIMING = "timing"
    COMPETITOR = "competitor"
    NO_RESPONSE = "no_response"
    OTHER = "other"
