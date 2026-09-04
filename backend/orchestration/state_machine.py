"""Formal Job state machine enforcing deterministic status transitions.

Authoritative transition table — v3.0.
All state changes MUST go through JobMutationService.transition() which enforces
the conditional UPDATE WHERE version = :expected_version optimistic lock.
No direct job.status assignment is permitted in worker code.
"""

from typing import Dict, Set


class StateMachineError(Exception):
    """Base error for state machine violations."""
    pass


class InvalidStateTransitionError(StateMachineError):
    """Raised when an illegal status transition is attempted on a Job."""
    pass


class StaleJobError(StateMachineError):
    """Raised when optimistic concurrency check fails due to version mismatch."""
    pass


class LeaseConflictError(StateMachineError):
    """Raised when an operation is attempted by a worker not holding the active lease."""
    pass


# ── Status constants ─────────────────────────────────────────────────────────

STATUS_PENDING = "PENDING"
STATUS_CLONING = "CLONING"
STATUS_ANALYZING = "ANALYZING"
STATUS_REVIEWING = "REVIEWING"
STATUS_REPAIRING = "REPAIRING"
STATUS_VALIDATING = "VALIDATING"
STATUS_PUBLISHING = "PUBLISHING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_ESCALATED = "ESCALATED"       # Human escalation — distinct from technical failure
STATUS_STALE_SNAPSHOT = "STALE_SNAPSHOT"  # Job superseded by a newer commit on the same PR

TERMINAL_STATES: Set[str] = {
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_ESCALATED,       # Repair budget exhausted, validation permanently failed, human-required
    STATUS_STALE_SNAPSHOT,  # Superseded by new commit — links to superseded_by_job_id
}

# ── Formal directed transition graph ─────────────────────────────────────────
# Each key may only transition to the values in its set.
# PUBLISHING → PUBLISHING is valid: retryable GitHub errors leave the job in
# PUBLISHING while the recovery worker schedules a retry via PublishedReview.
TRANSITION_GRAPH: Dict[str, Set[str]] = {
    STATUS_PENDING: {
        STATUS_CLONING,
        STATUS_FAILED,
        STATUS_STALE_SNAPSHOT,
    },
    STATUS_CLONING: {
        STATUS_ANALYZING,
        STATUS_FAILED,
        STATUS_STALE_SNAPSHOT,
    },
    STATUS_ANALYZING: {
        STATUS_REVIEWING,
        STATUS_FAILED,
        STATUS_STALE_SNAPSHOT,
    },
    STATUS_REVIEWING: {
        STATUS_REPAIRING,        # auto_repair=True, actionable findings, risk ≠ CRITICAL
        STATUS_PUBLISHING,       # auto_repair=False OR risk=CRITICAL OR no actionable findings
        STATUS_ESCALATED,        # Human escalation condition detected by reviewer
        STATUS_FAILED,
        STATUS_STALE_SNAPSHOT,
    },
    STATUS_REPAIRING: {
        STATUS_VALIDATING,       # Repair iteration complete
        STATUS_ESCALATED,        # Budget exhausted / stall detected
        STATUS_FAILED,
        STATUS_STALE_SNAPSHOT,
    },
    STATUS_VALIDATING: {
        STATUS_REPAIRING,        # Differential fails, retry budget remains
        STATUS_PUBLISHING,       # Zero new regressions — differential passed
        STATUS_ESCALATED,        # Budget exhausted
        STATUS_FAILED,
        STATUS_STALE_SNAPSHOT,
    },
    STATUS_PUBLISHING: {
        STATUS_COMPLETED,        # PublishedReview → POSTED
        STATUS_PUBLISHING,       # Retryable GitHub error — recovery worker will retry
        STATUS_FAILED,           # Permanent GitHub error (403, 404, 422)
        STATUS_STALE_SNAPSHOT,
    },
    # Terminal states: no outbound transitions
    STATUS_COMPLETED: set(),
    STATUS_FAILED: set(),
    STATUS_ESCALATED: set(),
    STATUS_STALE_SNAPSHOT: set(),
}

# ── Completion reasons — machine-readable, never parsed from error_message ───

COMPLETION_REASON_HUMAN_ESCALATION = "HUMAN_ESCALATION"
COMPLETION_REASON_REPAIR_BUDGET_EXHAUSTED = "REPAIR_BUDGET_EXHAUSTED"
COMPLETION_REASON_VALIDATION_FAILED = "VALIDATION_FAILED"
COMPLETION_REASON_STALE_SNAPSHOT_DRIFT = "STALE_SNAPSHOT_DRIFT"
COMPLETION_REASON_UNRECOVERABLE_ERROR = "UNRECOVERABLE_ERROR"
COMPLETION_REASON_PUBLICATION_FAILED = "PUBLICATION_FAILED"


# ── Transition helpers ───────────────────────────────────────────────────────

def can_transition(current_status: str, target_status: str) -> bool:
    """Check whether a transition between two statuses is permissible."""
    allowed = TRANSITION_GRAPH.get(current_status, set())
    return target_status in allowed


def validate_transition(current_status: str, target_status: str) -> None:
    """Validate transition or raise InvalidStateTransitionError."""
    if not can_transition(current_status, target_status):
        raise InvalidStateTransitionError(
            f"Illegal job transition from '{current_status}' to '{target_status}'. "
            f"Allowed from '{current_status}': {TRANSITION_GRAPH.get(current_status, set())}"
        )


def is_terminal(status: str) -> bool:
    """Return True if the given status is a terminal state."""
    return status in TERMINAL_STATES


def is_active(status: str) -> bool:
    """Return True if the job is in an active (non-terminal) state."""
    return status not in TERMINAL_STATES
