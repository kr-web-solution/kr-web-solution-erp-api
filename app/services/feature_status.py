"""Change Request (ProjectFeature) status transitions."""

from __future__ import annotations

from typing import Optional

from ..core.exceptions import BusinessRuleError, PermissionDeniedException

# Expanded CR lifecycle (migrated from requested/approved/in_progress/done/rejected)
CR_STATUSES = frozenset(
    {
        "submitted",
        "under_review",
        "estimation_pending",
        "quotation_sent",
        "awaiting_approval",
        "revision_requested",
        "approved",
        "in_development",
        "testing",
        "delivered",
        "completed",
        "rejected",
        "cancelled",
    }
)

# Legacy → new (for migration / API compatibility reads)
LEGACY_STATUS_MAP = {
    "requested": "submitted",
    "in_progress": "in_development",
    "done": "completed",
}

# Clients see their own submissions plus approved commercial/delivery states
CLIENT_VISIBLE = frozenset(
    {
        "submitted",
        "under_review",
        "estimation_pending",
        "quotation_sent",
        "awaiting_approval",
        "revision_requested",
        "approved",
        "in_development",
        "testing",
        "delivered",
        "completed",
        "rejected",
        "cancelled",
    }
)

ALLOWED: dict[str, frozenset[str]] = {
    "submitted": frozenset({"under_review", "rejected", "cancelled"}),
    "under_review": frozenset(
        {"estimation_pending", "awaiting_approval", "rejected", "cancelled", "revision_requested"}
    ),
    "estimation_pending": frozenset(
        {"quotation_sent", "awaiting_approval", "rejected", "cancelled", "revision_requested"}
    ),
    "quotation_sent": frozenset(
        {"awaiting_approval", "revision_requested", "rejected", "cancelled"}
    ),
    "awaiting_approval": frozenset(
        {"approved", "revision_requested", "rejected", "cancelled"}
    ),
    "revision_requested": frozenset(
        {"under_review", "estimation_pending", "quotation_sent", "cancelled", "rejected"}
    ),
    "approved": frozenset({"in_development", "cancelled"}),
    "in_development": frozenset({"testing", "cancelled"}),
    "testing": frozenset({"delivered", "in_development"}),
    "delivered": frozenset({"completed", "in_development"}),
    "completed": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
}

# Workers on assigned projects may move delivery statuses only
WORKER_ALLOWED: dict[str, frozenset[str]] = {
    "in_development": frozenset({"testing"}),
    "testing": frozenset({"delivered", "in_development"}),
    "delivered": frozenset({"in_development"}),
}

REQUEST_TYPES = frozenset(
    {
        "new_feature",
        "feature_enhancement",
        "existing_feature_modification",
        "third_party_integration",
        "scope_extension",
        "bug_fix",  # retained for historical rows
        "change_request",  # legacy alias
        "enhancement",  # legacy alias
    }
)

REASON_REQUIRED = frozenset({"rejected", "cancelled"})


class FeatureStatusError(BusinessRuleError):
    pass


def normalize_status(status: Optional[str]) -> str:
    s = (status or "submitted").strip()
    return LEGACY_STATUS_MAP.get(s, s)


def assert_known(status: str) -> None:
    if status not in CR_STATUSES:
        raise FeatureStatusError(f"Invalid change-request status '{status}'")


def assert_transition(
    current: str,
    target: str,
    *,
    privileged: bool,
    reason: Optional[str] = None,
    is_billable: bool = True,
) -> None:
    cur = normalize_status(current)
    tgt = normalize_status(target)
    assert_known(cur)
    assert_known(tgt)
    if cur == tgt:
        return
    graph = ALLOWED if privileged else WORKER_ALLOWED
    if tgt not in graph.get(cur, frozenset()):
        if not privileged:
            raise PermissionDeniedException(
                f"Workers cannot change change-request status from {cur} to {tgt}"
            )
        raise FeatureStatusError(f"Cannot transition change request from {cur} to {tgt}")

    if tgt == "in_development" and cur != "approved" and privileged:
        # Only from approved (graph already enforces); keep explicit message
        if cur != "approved":
            raise FeatureStatusError("Only approved change requests can enter development")

    if tgt in REASON_REQUIRED and not (reason or "").strip():
        raise FeatureStatusError(f"A reason is required when setting status to {tgt}")

    if tgt == "approved" and not is_billable and not privileged:
        raise PermissionDeniedException("Only founders can approve no-charge change requests")
