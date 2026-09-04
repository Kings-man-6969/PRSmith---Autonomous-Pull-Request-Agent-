"""Review and repair exception hierarchy for PRSmith security and correctness invariants."""


class ReviewError(Exception):
    """Base exception for all review and repair pipeline failures."""
    pass


class RepairPreconditionError(ReviewError):
    """Raised when mandatory evidence or graph preconditions for repair are not met."""
    pass


class StaleFindingError(RepairPreconditionError):
    """Raised when finding references an obsolete AST graph version."""
    pass


class StaleSnapshotError(RepairPreconditionError):
    """Raised when repository commit SHA has drifted from the snapshot under review."""
    pass


class DenylistViolation(RepairPreconditionError):
    """Raised when a patch attempts to modify protected, infrastructure, or secret files."""
    pass


class PublicationPrerequisiteError(ReviewError):
    """Raised when required validation run or evidence check has not passed before publication."""
    pass


class PublicationRetryableError(ReviewError):
    """Raised on transient GitHub API errors (timeout, 429, 5xx).

    The recovery worker will schedule a retry based on next_attempt_at.
    The Job stays in PUBLISHING while PublishedReview.publication_status = FAILED_RETRYABLE.
    """
    def __init__(self, message: str, retry_after_seconds: int = 0) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PublicationPermanentError(ReviewError):
    """Raised on permanent GitHub API errors (403 Forbidden, 404 Not Found, 422 Unprocessable).

    The Job transitions to FAILED and PublishedReview.publication_status = FAILED_PERMANENT.
    """
    def __init__(self, message: str, http_status: int = 0) -> None:
        super().__init__(message)
        self.http_status = http_status


class AuthInsufficientScope(ReviewError):
    """Raised when the GitHub OAuth token lacks the required scope for the operation.

    Do not retry — user must re-authorize with the required scopes.
    """
    def __init__(self, message: str, required_scope: str = "", available_scopes: str = "") -> None:
        super().__init__(message)
        self.required_scope = required_scope
        self.available_scopes = available_scopes


class AuthTemporaryFailure(ReviewError):
    """Raised when no valid GitHub credential is available for the operation.

    May occur if the App installation token has expired and refresh failed,
    or if the user OAuth token has been revoked. Treat as PublicationRetryableError
    after credential refresh attempt; escalate if refresh fails.
    """
    pass
