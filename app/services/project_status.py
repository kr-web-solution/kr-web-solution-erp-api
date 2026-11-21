"""Canonical project status transitions and completion gates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from ..common.workflow import HANDOVER_PHASE
from ..core.exceptions import BusinessRuleError, PermissionDeniedException


PROJECT_STATUSES = frozenset({"lead", "running", "on_hold", "stopped", "completed"})

# Privileged full graph. Workers use WORKER_ALLOWED_TRANSITIONS only.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "lead": frozenset({"running", "on_hold", "stopped"}),
    "running": frozenset({"on_hold", "stopped", "completed", "lead"}),
    "on_hold": frozenset({"running", "stopped"}),
    "stopped": frozenset({"running", "lead"}),
    "completed": frozenset({"running"}),
}

WORKER_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "running": frozenset({"on_hold"}),
    "on_hold": frozenset({"running"}),
}

REOPEN_FROM = frozenset({"stopped", "completed"})


class ProjectStatusError(BusinessRuleError):
    """Illegal project status transition or completion gate violation."""


def assert_known_status(status: str) -> None:
    if status not in PROJECT_STATUSES:
        raise ProjectStatusError(f"Invalid project status '{status}'")


def can_transition(current: str, target: str, *, privileged: bool) -> bool:
    if current == target:
        return True
    graph = ALLOWED_TRANSITIONS if privileged else WORKER_ALLOWED_TRANSITIONS
    return target in graph.get(current, frozenset())


def assert_transition(
    current: str,
    target: str,
    *,
    privileged: bool,
    reason: Optional[str] = None,
) -> None:
    assert_known_status(current)
    assert_known_status(target)
    if current == target:
        return
    if not can_transition(current, target, privileged=privileged):
        if not privileged:
            raise PermissionDeniedException(
                f"Workers can only move between running and on hold "
                f"(cannot change {current} → {target})"
            )
        raise ProjectStatusError(f"Cannot transition project from {current} to {target}")

    if privileged and current in REOPEN_FROM and target == "running":
        if not (reason or "").strip():
            raise ProjectStatusError(
                "A reopen reason is required when reopening a stopped or completed project"
            )

    if privileged and target == "stopped" and current != "stopped":
        # Reason optional but recommended; keep optional for UX parity with pause flow.
        pass


def current_phase_checklist_complete(
    checklist_items: Iterable,
    delivery_phase: int,
) -> bool:
    phase = int(delivery_phase or 0)
    stage_items = [i for i in (checklist_items or []) if getattr(i, "stage_number", None) == phase]
    if not stage_items:
        return True
    return all(bool(getattr(i, "is_done", False)) for i in stage_items)


def assert_can_complete(
    *,
    delivery_phase: int,
    checklist_items: Iterable,
    force: bool = False,
    force_reason: Optional[str] = None,
) -> None:
    """Block status=completed unless delivery is far enough and current checklist is done."""
    phase = int(delivery_phase or 0)
    if not current_phase_checklist_complete(checklist_items, phase):
        raise ProjectStatusError(
            "Complete the current delivery checklist before marking the project completed"
        )
    if phase < HANDOVER_PHASE:
        if force and (force_reason or "").strip():
            return
        raise ProjectStatusError(
            f"Project can only be completed from handover (phase {HANDOVER_PHASE}) or later. "
            "Advance delivery first, or pass force_complete with a reason."
        )


def apply_status_fields(
    project,
    *,
    new_status: str,
    actor_id: Optional[str],
    reason: Optional[str] = None,
) -> None:
    """Mutate project reopen metadata when leaving terminal-ish states."""
    prev = project.status
    project.status = new_status
    if reason is not None:
        project.status_reason = (reason or "").strip() or None
    if prev in REOPEN_FROM and new_status == "running":
        project.reopened_at = datetime.now(timezone.utc)
        project.reopened_by = actor_id
