"""Review publication service — atomic claiming, marker reconciliation, and crash recovery.

Publication state machine for PublishedReview.publication_status:
    CLAIMED → POSTING → POSTED           (happy path)
    CLAIMED → POSTING → FAILED_RETRYABLE (recovery worker retries)
    CLAIMED → POSTING → FAILED_PERMANENT (Job → FAILED)
    CLAIMED → CANCELLED                  (obsolete — superseded by newer lineage)

Concurrency guarantees:
    - Partial unique index on (repository_id, pr_number) WHERE status IN ('CLAIMED','POSTING')
      ensures at most one active claim per PR.
    - cancel_obsolete_publication_claim() must be called before claim_publication()
      so stale CLAIMING rows from older lineages do not block newer runs.
    - is_current swap is performed inside a single transaction.

Idempotency:
    - GitHub marker: <!-- prsmith:review:{repo_id}:{pr_number} --> (PR-level, stable)
    - Chain hash embedded in comment body for audit trail only.
    - If marker found on GitHub: PATCH (edit) the existing comment — one PRSmith comment per PR.
    - If marker absent: POST new comment.

Delivery guarantee: at-least-once + idempotent/reconcilable side effects.
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database.models import Job, PublishedReview, ReviewRun, ValidationRun, utcnow
from backend.observability.logging import get_logger, log_event
from backend.review.exceptions import (
    PublicationPermanentError,
    PublicationPrerequisiteError,
    PublicationRetryableError,
)

logger = get_logger(__name__)

# Permanent GitHub HTTP status codes — no point retrying
_PERMANENT_HTTP_ERRORS = {403, 404, 410, 422}
# Retryable HTTP status codes
_RETRYABLE_HTTP_ERRORS = {408, 429, 500, 502, 503, 504}

STALE_POSTING_THRESHOLD_MINUTES = 10
MAX_PUBLICATION_ATTEMPTS = 5


def _backoff_seconds(attempts: int) -> int:
    """Exponential backoff: min(60 * 2^(attempts-1), 3600) seconds."""
    return min(int(60 * math.pow(2, max(0, attempts - 1))), 3600)


# ── Prerequisites ──────────────────────────────────────────────────────────────

async def assert_publication_prerequisites(
    session: AsyncSession,
    job: Job,
    patch_artifact_hash: Optional[str],
) -> Any:
    """Validate mandatory prerequisites before publication claim is acquired.

    Two distinct paths:
    1. Autonomous repair path: requires passing ValidationRun for the exact patch and head_sha.
    2. Review-only path: requires passing evidence validation for the exact head_sha.

    Raises PublicationPrerequisiteError if prerequisites are not met.
    """
    if patch_artifact_hash is not None:
        validation_run = await session.scalar(
            select(ValidationRun).where(
                ValidationRun.job_id == job.id,
                ValidationRun.head_sha == job.head_sha,
                ValidationRun.patch_artifact_hash == patch_artifact_hash,
                ValidationRun.passed.is_(True),
            )
        )
        if not validation_run:
            raise PublicationPrerequisiteError(
                f"Autonomous repair publication blocked: No passing ValidationRun found for "
                f"job={job.id}, head_sha={job.head_sha}, patch={patch_artifact_hash}."
            )
        return validation_run
    else:
        review_run = await session.scalar(
            select(ReviewRun).where(
                ReviewRun.job_id == job.id,
                ReviewRun.head_sha == job.head_sha,
                ReviewRun.evidence_validation_passed.is_(True),
            )
        )
        if not review_run:
            raise PublicationPrerequisiteError(
                f"Review-only publication blocked: No passing evidence validation found for "
                f"job={job.id}, head_sha={job.head_sha}."
            )
        return review_run


# ── Obsolete Claim Cancellation ────────────────────────────────────────────────

async def cancel_obsolete_publication_claim(
    session: AsyncSession,
    repository_id: str,
    pr_number: int,
    new_head_sha: str,
) -> int:
    """Cancel any active (CLAIMED/POSTING) PublishedReview belonging to a superseded lineage.

    Called before claim_publication() so stale POSTING rows from older pipeline
    runs do not block newer publications via the partial unique index.

    An obsolete claim is one where head_sha != new_head_sha and status is active.

    Returns the number of rows cancelled.
    """
    stmt = (
        update(PublishedReview)
        .where(
            PublishedReview.repository_id == repository_id,
            PublishedReview.pr_number == pr_number,
            PublishedReview.publication_status.in_(["CLAIMED", "POSTING"]),
            PublishedReview.head_sha != new_head_sha,
        )
        .values(publication_status="CANCELLED")
        .returning(PublishedReview.id)
    )
    result = await session.execute(stmt)
    cancelled_ids = result.fetchall()
    if cancelled_ids:
        await session.flush()
        logger.info(
            "Cancelled obsolete active publication claims",
            repository_id=repository_id,
            pr_number=pr_number,
            cancelled_count=len(cancelled_ids),
        )
    return len(cancelled_ids)


# ── Claim ──────────────────────────────────────────────────────────────────────

async def claim_publication(
    session: AsyncSession,
    idempotency_key: str,
    chain_hash: str,
    job: Job,
    repository_id: Optional[str] = None,
    pr_number: Optional[int] = None,
    worker_id: Optional[str] = None,
    patch_artifact_hash: Optional[str] = None,
    validation_run_id: Optional[str] = None,
    review_status: str = "CONFIRMED",
    patch_status: str = "NONE",
) -> Optional[PublishedReview]:
    """Atomically claim publication ownership in the database.

    The partial unique index on (repository_id, pr_number) WHERE status IN ('CLAIMED','POSTING')
    enforces that at most one worker owns publication for a given PR at any time.
    Call cancel_obsolete_publication_claim() first if a newer lineage is superseding an old one.

    Returns:
        PublishedReview row if claim was granted.
        None if another worker already claimed this exact lineage (idempotency_key collision).

    Raises:
        IntegrityError re-raised if the active-claim partial index fires (two workers racing
        for the same new lineage on the same PR). Caller should back off and retry.
    """
    now = utcnow()
    repo_id = repository_id or job.repository_id
    pr_num = pr_number if pr_number is not None else job.pr_number
    try:
        pub = PublishedReview(
            job_id=job.id,
            repository_id=repo_id,
            pr_number=pr_num,
            idempotency_key=idempotency_key,
            chain_hash=chain_hash,
            head_sha=job.head_sha,
            patch_artifact_hash=patch_artifact_hash,
            validation_run_id=validation_run_id,
            review_status=review_status,
            patch_status=patch_status,
            publication_status="CLAIMED",
            is_current=False,
            worker_id=worker_id,
            claimed_at=now,
            last_attempt_at=None,
            next_attempt_at=None,
            publication_attempts=0,
            max_publication_attempts=MAX_PUBLICATION_ATTEMPTS,
            reviewed_commit_sha=job.head_sha,
            pipeline_version=settings.PIPELINE_VERSION,
        )
        async with session.begin_nested():
            session.add(pub)
            await session.flush()
        await session.commit()
        log_event(
            "publication.claimed",
            job_id=job.id,
            idempotency_key=idempotency_key,
            worker_id=worker_id,
        )
        return pub
    except IntegrityError:
        await session.rollback()
        logger.info(
            "Publication already claimed or active-PR index fired",
            idempotency_key=idempotency_key,
        )
        return None


# ── GitHub Interaction ─────────────────────────────────────────────────────────

def _stable_pr_marker(repository_id: str, pr_number: int) -> str:
    """Stable PR-level HTML marker for deduplication. One per PR, regardless of lineage."""
    return f"<!-- prsmith:review:{repository_id}:{pr_number} -->"


async def publish_review_comment(
    session: AsyncSession,
    pub: PublishedReview,
    repo_full_name: str,
    pr_number: int,
    review_body: str,
    github_client: Any,
    token: Optional[str] = None,
) -> PublishedReview:
    """Publish or update the PRSmith review comment on GitHub.

    Option B: one active PRSmith comment per PR.
      1. Advance publication_status to POSTING, increment attempt counter.
      2. Search GitHub for existing comment with PR-level marker.
      3. If found: PATCH (edit) the comment — no duplicate.
      4. If not found: POST new comment.
      5. Atomically set is_current=True (clear previous is_current row first).

    Raises:
        PublicationRetryableError: transient GitHub errors — recovery worker retries.
        PublicationPermanentError: permanent GitHub errors — Job → FAILED.
    """
    now = utcnow()
    from sqlalchemy.inspection import inspect as sa_inspect
    pub_state = sa_inspect(pub, raiseerr=False)
    pub_id = pub_state.identity[0] if (pub_state and pub_state.identity) else None
    if pub_id is not None:
        fresh_pub = await session.get(PublishedReview, pub_id)
        if fresh_pub is not None:
            pub = fresh_pub
    # Advance to POSTING + increment attempt counter
    pub.publication_status = "POSTING"
    pub.publication_attempts += 1
    pub.last_attempt_at = now
    await session.commit()

    marker = _stable_pr_marker(str(pub.repository_id), pub.pr_number)
    full_body = f"{marker}\n\n{review_body}"

    try:
        # Check for existing PRSmith comment (crash recovery + Option B)
        existing_comment = None
        if hasattr(github_client, "find_comment_by_marker"):
            try:
                existing_comment = await github_client.find_comment_by_marker(
                    repo=repo_full_name,
                    pr_number=pr_number,
                    marker=marker,
                    token=token,
                )
            except TypeError:
                existing_comment = await github_client.find_comment_by_marker(
                    repo=repo_full_name,
                    pr_number=pr_number,
                    marker=marker,
                )

        if existing_comment:
            comment_id = str(existing_comment.get("id", ""))
            # PATCH the existing comment (Option B — edit, not duplicate)
            if hasattr(github_client, "edit_review_comment_with_token"):
                await github_client.edit_review_comment_with_token(
                    access_token=token,
                    owner=repo_full_name.split("/")[0],
                    repo=repo_full_name.split("/")[-1],
                    comment_id=comment_id,
                    body=full_body,
                )
            log_event("publication.updated_existing", idempotency_key=pub.idempotency_key, comment_id=comment_id)
        else:
            # POST new comment
            comment_resp = await _post_comment(github_client, repo_full_name, pr_number, full_body, token)
            comment_id = str(comment_resp.get("id") if isinstance(comment_resp, dict) else getattr(comment_resp, "id", ""))
            log_event("publication.posted_new", idempotency_key=pub.idempotency_key, comment_id=comment_id)

    except PublicationRetryableError:
        # Leave in FAILED_RETRYABLE — recovery worker will retry
        pub.publication_status = "FAILED_RETRYABLE"
        pub.next_attempt_at = now + timedelta(seconds=_backoff_seconds(pub.publication_attempts))
        if pub.publication_attempts >= pub.max_publication_attempts:
            pub.publication_status = "FAILED_PERMANENT"
            pub.next_attempt_at = None
        await session.commit()
        raise
    except PublicationPermanentError:
        pub.publication_status = "FAILED_PERMANENT"
        pub.next_attempt_at = None
        await session.commit()
        raise

    # ── Success: atomic is_current swap (single transaction) ─────────────────
    pub.github_comment_id = comment_id
    pub.posted_at = now
    pub.next_attempt_at = None
    # First clear any existing current row for this PR
    await session.execute(
        update(PublishedReview)
        .where(
            PublishedReview.repository_id == pub.repository_id,
            PublishedReview.pr_number == pub.pr_number,
            PublishedReview.is_current.is_(True),
            PublishedReview.id != pub.id,
        )
        .values(is_current=False)
    )
    # Then mark this one current
    pub.publication_status = "POSTED"
    pub.is_current = True
    await session.commit()
    # Partial unique index is the final enforcement — if the above commit raises
    # IntegrityError, the swap must be retried.

    log_event("publication.posted", idempotency_key=pub.idempotency_key, comment_id=pub.github_comment_id)
    return pub


async def _post_comment(
    github_client: Any,
    repo_full_name: str,
    pr_number: int,
    body: str,
    token: Optional[str],
) -> dict:
    """Post a new PR comment, translating GitHub HTTP errors to typed exceptions."""
    try:
        if token and hasattr(github_client, "post_review_comment_with_token"):
            resp = await github_client.post_review_comment_with_token(
                access_token=token,
                owner=repo_full_name.split("/")[0],
                repo=repo_full_name.split("/")[-1],
                pr_number=pr_number,
                body=body,
            )
        else:
            resp = await github_client.post_review_comment(
                repo=repo_full_name,
                pr_number=pr_number,
                body=body,
            )
        return resp if isinstance(resp, dict) else {"id": getattr(resp, "id", "")}
    except Exception as e:
        _classify_github_error(e)
        raise  # unreachable — _classify_github_error always raises


def _classify_github_error(exc: Exception) -> None:
    """Translate GitHub API exceptions to PublicationRetryableError or PublicationPermanentError."""
    msg = str(exc)
    http_status = getattr(exc, "status_code", 0) or getattr(exc, "response", None) and getattr(exc.response, "status_code", 0) or 0

    if http_status in _PERMANENT_HTTP_ERRORS:
        raise PublicationPermanentError(
            f"GitHub permanent error {http_status}: {msg}", http_status=http_status
        )
    if http_status in _RETRYABLE_HTTP_ERRORS:
        retry_after = 0
        raise PublicationRetryableError(
            f"GitHub transient error {http_status}: {msg}", retry_after_seconds=retry_after
        )
    # Connection errors, timeouts, unknown — treat as retryable
    raise PublicationRetryableError(f"GitHub connection/unknown error: {msg}")


# ── Stale POSTING Recovery ─────────────────────────────────────────────────────

async def recover_stale_posting_claims(
    session: AsyncSession,
    stale_threshold_minutes: int = STALE_POSTING_THRESHOLD_MINUTES,
    github_client: Optional[Any] = None,
) -> List[PublishedReview]:
    """Recovery worker: scan for PublishedReview rows stuck in POSTING beyond threshold.

    For each stale POSTING row:
      1. Call find_comment_by_marker() on GitHub using the PR-level stable marker.
      2. Marker found → crashed after GitHub accepted — advance to POSTED (no duplicate).
      3. Marker absent, retryable attempts remain → reset to FAILED_RETRYABLE + schedule retry.
      4. Marker absent, max attempts exceeded → FAILED_PERMANENT.

    Also processes FAILED_RETRYABLE rows whose next_attempt_at <= NOW().
    """
    now = utcnow()
    stale_threshold = now - timedelta(minutes=stale_threshold_minutes)

    # Find stale POSTING rows
    stale_stmt = select(PublishedReview).where(
        PublishedReview.publication_status == "POSTING",
        PublishedReview.last_attempt_at.isnot(None),
        PublishedReview.last_attempt_at < stale_threshold,
    )
    stale_result = await session.execute(stale_stmt)
    stale_rows = list(stale_result.scalars().all())

    # Find FAILED_RETRYABLE rows ready for retry
    retry_stmt = select(PublishedReview).where(
        PublishedReview.publication_status == "FAILED_RETRYABLE",
        PublishedReview.next_attempt_at.isnot(None),
        PublishedReview.next_attempt_at <= now,
    )
    retry_result = await session.execute(retry_stmt)
    retry_rows = list(retry_result.scalars().all())

    processed: List[PublishedReview] = []

    for pub in stale_rows + retry_rows:
        marker = _stable_pr_marker(str(pub.repository_id), pub.pr_number)
        existing = None

        if github_client and hasattr(github_client, "find_comment_by_marker"):
            try:
                existing = await github_client.find_comment_by_marker(
                    repo=None,  # Must load repo full_name from DB if needed
                    pr_number=pub.pr_number,
                    marker=marker,
                )
            except Exception as e:
                logger.warning("Could not query GitHub for marker during recovery", error=str(e))

        if existing:
            # Crash-after-POST: marker found — reconcile to POSTED without duplicating
            pub.github_comment_id = str(existing.get("id", ""))
            pub.posted_at = now
            pub.next_attempt_at = None
            # Atomic is_current swap
            await session.execute(
                update(PublishedReview)
                .where(
                    PublishedReview.repository_id == pub.repository_id,
                    PublishedReview.pr_number == pub.pr_number,
                    PublishedReview.is_current.is_(True),
                    PublishedReview.id != pub.id,
                )
                .values(is_current=False)
            )
            pub.publication_status = "POSTED"
            pub.is_current = True
            log_event("publication.reconciled", idempotency_key=pub.idempotency_key, comment_id=pub.github_comment_id)
        else:
            # Marker absent — increment and schedule retry or mark permanent failure
            pub.publication_attempts += 1
            if pub.publication_attempts >= pub.max_publication_attempts:
                pub.publication_status = "FAILED_PERMANENT"
                pub.next_attempt_at = None
                logger.error(
                    "Publication permanently failed after max attempts",
                    idempotency_key=pub.idempotency_key,
                    attempts=pub.publication_attempts,
                )
            else:
                pub.publication_status = "FAILED_RETRYABLE"
                pub.next_attempt_at = now + timedelta(seconds=_backoff_seconds(pub.publication_attempts))
                logger.info(
                    "Publication retry scheduled",
                    idempotency_key=pub.idempotency_key,
                    attempt=pub.publication_attempts,
                    next_attempt_at=pub.next_attempt_at,
                )

        processed.append(pub)

    if processed:
        await session.commit()
        logger.info("Publication recovery run complete", processed=len(processed))

    return processed


async def get_current_published_review(
    session: AsyncSession,
    repository_id: str,
    pr_number: int,
) -> Optional[PublishedReview]:
    """Return the current (POSTED, is_current=True) PublishedReview for a PR, if any."""
    stmt = select(PublishedReview).where(
        PublishedReview.repository_id == repository_id,
        PublishedReview.pr_number == pr_number,
        PublishedReview.is_current.is_(True),
        PublishedReview.publication_status == "POSTED",
    )
    return await session.scalar(stmt)


async def publish_review(
    session: AsyncSession,
    job: Job,
    repo_full_name: str,
    pr_number: int,
    review_body: str,
    github_client: Any,
    worker_id: Optional[str] = None,
    patch_artifact_hash: Optional[str] = None,
    graph_content_hash: str = "",
    finding_content_hashes: Optional[List[str]] = None,
    validation_result_hash: str = "no_validation",
    pipeline_version: Optional[str] = None,
    token: Optional[str] = None,
) -> Optional[PublishedReview]:
    """Complete publication flow: prerequisites → cancel obsolete → claim → post."""
    from backend.orchestration.hashing import compute_chain_hash
    from backend.orchestration.idempotency import make_publication_lineage_key

    await assert_publication_prerequisites(session, job, patch_artifact_hash)

    chain_hash = compute_chain_hash(
        snapshot_sha=job.head_sha,
        graph_content_hash=graph_content_hash,
        finding_content_hashes=finding_content_hashes or [],
        patch_artifact_hash=patch_artifact_hash,
        validation_result_hash=validation_result_hash,
        pipeline_version=pipeline_version or settings.PIPELINE_VERSION,
    )
    lineage_key = make_publication_lineage_key(str(job.repository_id), pr_number, chain_hash)

    await cancel_obsolete_publication_claim(
        session,
        repository_id=str(job.repository_id),
        pr_number=pr_number,
        new_head_sha=job.head_sha,
    )

    pub = await claim_publication(
        session=session,
        idempotency_key=lineage_key,
        chain_hash=chain_hash,
        job=job,
        repository_id=str(job.repository_id),
        pr_number=pr_number,
        worker_id=worker_id,
        patch_artifact_hash=patch_artifact_hash,
    )
    if pub is None:
        return None

    return await publish_review_comment(
        session=session,
        pub=pub,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        review_body=review_body,
        github_client=github_client,
        token=token,
    )
