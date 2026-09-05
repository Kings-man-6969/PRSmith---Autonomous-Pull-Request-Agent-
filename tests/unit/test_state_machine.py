"""Unit tests for formal Job state machine transition rules."""

import pytest

from backend.orchestration.state_machine import (
    InvalidStateTransitionError,
    can_transition,
    validate_transition,
)


def test_valid_forward_transitions():
    """Legal forward lifecycle paths succeed."""
    assert can_transition("PENDING", "CLONING")
    assert can_transition("CLONING", "ANALYZING")
    assert can_transition("ANALYZING", "REVIEWING")
    assert can_transition("REVIEWING", "REPAIRING")
    assert can_transition("REPAIRING", "VALIDATING")
    assert can_transition("VALIDATING", "PUBLISHING")
    assert can_transition("PUBLISHING", "COMPLETED")

    # Review-only path (auto_repair=False or clean PR)
    assert can_transition("REVIEWING", "PUBLISHING")

    validate_transition("PENDING", "CLONING")
    validate_transition("CLONING", "ANALYZING")
    validate_transition("ANALYZING", "REVIEWING")
    validate_transition("REVIEWING", "PUBLISHING")
    validate_transition("PUBLISHING", "COMPLETED")


def test_repair_iteration_loop():
    """Validating can loop back to repairing for multi-pass repair."""
    assert can_transition("REVIEWING", "REPAIRING")
    assert can_transition("REPAIRING", "VALIDATING")
    assert can_transition("VALIDATING", "REPAIRING")
    assert can_transition("VALIDATING", "PUBLISHING")


def test_superseded_and_failed_reachable_from_active_states():
    """Active states can always transition to FAILED or STALE_SNAPSHOT/SUPERSEDED upon drift or error."""
    for state in ["PENDING", "CLONING", "ANALYZING", "REVIEWING", "REPAIRING", "VALIDATING", "PUBLISHING"]:
        assert can_transition(state, "FAILED")
        assert can_transition(state, "STALE_SNAPSHOT")
        assert can_transition(state, "SUPERSEDED")


def test_illegal_state_transitions_raise_error():
    """Illegal transitions and jumps throw InvalidStateTransitionError."""
    # Cannot jump directly from PENDING to COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        validate_transition("PENDING", "COMPLETED")

    # Terminal states have no outgoing transitions
    with pytest.raises(InvalidStateTransitionError):
        validate_transition("COMPLETED", "REVIEWING")

    with pytest.raises(InvalidStateTransitionError):
        validate_transition("FAILED", "PENDING")

    with pytest.raises(InvalidStateTransitionError):
        validate_transition("SUPERSEDED", "ANALYZING")
