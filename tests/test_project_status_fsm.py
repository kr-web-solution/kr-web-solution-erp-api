"""Project status FSM unit tests."""

import pytest

from app.common.workflow import HANDOVER_PHASE
from app.core.exceptions import PermissionDeniedException
from app.services.project_status import (
    ProjectStatusError,
    assert_can_complete,
    assert_transition,
    can_transition,
)


def test_worker_only_running_on_hold():
    assert can_transition("running", "on_hold", privileged=False)
    assert can_transition("on_hold", "running", privileged=False)
    assert not can_transition("running", "completed", privileged=False)
    assert not can_transition("lead", "running", privileged=False)
    with pytest.raises(PermissionDeniedException):
        assert_transition("running", "completed", privileged=False)


def test_illegal_privileged_transition():
    with pytest.raises(ProjectStatusError, match="Cannot transition"):
        assert_transition("lead", "completed", privileged=True)


def test_reopen_requires_reason():
    with pytest.raises(ProjectStatusError, match="reopen reason"):
        assert_transition("completed", "running", privileged=True, reason=None)
    with pytest.raises(ProjectStatusError, match="reopen reason"):
        assert_transition("stopped", "running", privileged=True, reason="  ")
    assert_transition("completed", "running", privileged=True, reason="Client asked to resume")


def test_complete_blocked_before_handover():
    items = [type("I", (), {"stage_number": 5, "is_done": True})()]
    with pytest.raises(ProjectStatusError, match="handover"):
        assert_can_complete(delivery_phase=5, checklist_items=items)


def test_complete_blocked_incomplete_checklist():
    items = [
        type("I", (), {"stage_number": HANDOVER_PHASE, "is_done": True})(),
        type("I", (), {"stage_number": HANDOVER_PHASE, "is_done": False})(),
    ]
    with pytest.raises(ProjectStatusError, match="checklist"):
        assert_can_complete(delivery_phase=HANDOVER_PHASE, checklist_items=items)


def test_complete_ok_at_handover():
    items = [type("I", (), {"stage_number": HANDOVER_PHASE, "is_done": True})()]
    assert_can_complete(delivery_phase=HANDOVER_PHASE, checklist_items=items)


def test_force_complete_before_handover_with_reason():
    items = [type("I", (), {"stage_number": 8, "is_done": True})()]
    assert_can_complete(
        delivery_phase=8,
        checklist_items=items,
        force=True,
        force_reason="Legacy wrap-up",
    )
