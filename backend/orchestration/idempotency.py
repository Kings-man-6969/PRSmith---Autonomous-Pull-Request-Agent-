"""Canonical idempotency key builders binding pipeline version and artifact digests.

Key taxonomy:
    review idempotency key   — deduplicates Job dispatch (same PR + SHA = same logical review)
    repair idempotency key   — deduplicates repair attempts per finding + patch
    validation idempotency   — deduplicates validation runs per patch artifact
    publication lineage key  — per-artifact audit trail (unique per chain_hash)
    publication PR key       — stable PR-level key for UX queries ("what is current review?")
"""

from typing import Optional
from backend.config import settings


def make_review_idempotency_key(
    repo_id: str,
    pr_number: int,
    head_sha: str,
    pipeline_version: Optional[str] = None,
) -> str:
    """Canonical idempotency key for PR review analysis tasks.

    Used for Job.idempotency_key to prevent duplicate manual dispatch
    for the same PR + commit SHA + pipeline version.
    """
    version = pipeline_version or settings.PIPELINE_VERSION
    return f"rev:{repo_id}:{pr_number}:{head_sha}:{version}"


def make_repair_idempotency_key(
    repo_id: str,
    pr_number: int,
    head_sha: str,
    finding_hash: str,
    pipeline_version: Optional[str] = None,
) -> str:
    """Canonical idempotency key for patch synthesis and repair tasks."""
    version = pipeline_version or settings.PIPELINE_VERSION
    return f"rep:{repo_id}:{pr_number}:{head_sha}:{finding_hash}:{version}"


def make_validation_idempotency_key(
    repo_id: str,
    pr_number: int,
    head_sha: str,
    patch_artifact_hash: str,
    pipeline_version: Optional[str] = None,
) -> str:
    """Canonical idempotency key for isolated sandbox test validation runs."""
    version = pipeline_version or settings.PIPELINE_VERSION
    return f"val:{repo_id}:{pr_number}:{head_sha}:{patch_artifact_hash}:{version}"


def make_publication_lineage_key(
    repo_id: str,
    pr_number: int,
    chain_hash: str,
) -> str:
    """Per-artifact publication key for audit trail.

    Unique per pipeline lineage (chain_hash). Multiple lineage keys may exist per PR
    across different pipeline runs. Used as PublishedReview.idempotency_key (UNIQUE).

    NOT used for GitHub comment deduplication — use make_publication_pr_marker() for that.
    """
    return f"pub:{repo_id}:{pr_number}:{chain_hash}"


def make_publication_pr_key(
    repo_id: str,
    pr_number: int,
) -> str:
    """Stable PR-level publication identity key.

    Used for UX queries: "what is the current published review for this PR?"
    Corresponds to the HTML marker embedded in GitHub comments:
        <!-- prsmith:review:{repo_id}:{pr_number} -->

    One active PRSmith comment per PR (Option B). Chain hash retained inside
    the comment body for lineage auditability.
    """
    return f"pub:{repo_id}:{pr_number}"
