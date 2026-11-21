"""Change-request status FSM unit tests."""

import pytest

from app.core.exceptions import PermissionDeniedException
from app.services.feature_status import FeatureStatusError, assert_transition, normalize_status


def test_legacy_status_normalize():
    assert normalize_status("requested") == "submitted"
    assert normalize_status("in_progress") == "in_development"
    assert normalize_status("done") == "completed"


def test_unapproved_cannot_enter_development():
    with pytest.raises(FeatureStatusError):
        assert_transition("submitted", "in_development", privileged=True)


def test_approved_to_development():
    assert_transition("approved", "in_development", privileged=True)


def test_cancel_requires_reason():
    with pytest.raises(FeatureStatusError, match="reason"):
        assert_transition("submitted", "cancelled", privileged=True, reason=None)


def test_worker_cannot_approve():
    with pytest.raises(PermissionDeniedException):
        assert_transition("awaiting_approval", "approved", privileged=False)
