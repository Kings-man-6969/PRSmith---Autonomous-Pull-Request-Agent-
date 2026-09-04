"""Snapshot validation checkpoints and atomic job superseding."""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Job, utcnow
from backend.observability.logging import get_logger, log_event
from backend.review.exceptions import StaleSnapshotError

logger = get_logger(__name__)


def assert_head_sha_current(
    job: Job, current_head_sha: str, checkpoint_name: str = "checkpoint"
) -> None:
    """Validate that repository HEAD commit matches job.head_sha.

    Raises StaleSnapshotError if commit has drifted.
    Mandatory checkpoints: before review, before each repair, immediately before publication.
    """
    if current_head_sha.strip().lower() != job.head_sha.strip().lower():
        raise StaleSnapshotError(
            f"Commit drift detected at {checkpoint_name}: job head_sha={job.head_sha} "
            f"!= current repository head_sha={current_head_sha}. Job must be marked STALE_SNAPSHOT."
        )


async def mark_superseded(session: AsyncSession, job: Job, new_head_sha: str) -> Job:
    """Atomically supersede job with deterministic PostgreSQL advisory locking.

    1. Acquire transaction-level lock on (repository_id, pr_number) using hashtextextended.
    2. Re-check if a replacement job was already created concurrently.
    3. Create replacement Job in RECEIVED state.
    4. Link old job to new job via superseded_by_job_id.
    """
    bind = session.bind
    dialect_name = bind.dialect.name if bind else ""

    if "postgresql" in dialect_name:
        lock_identity = f"{job.repository_id}:{job.pr_number}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:s, 0)::bigint)"),
            {"s": lock_identity},
        )

    # Re-check if another worker already superseded this job
    existing = await session.scalar(
        select(Job).where(
            Job.repository_id == job.repository_id,
            Job.pr_number == job.pr_number,
            Job.head_sha == new_head_sha,
            Job.status != "STALE_SNAPSHOT",
            Job.superseded_by_job_id.is_(None),
        )
    )
    if existing:
        return existing

    new_job = Job(
        repository_id=job.repository_id,
        pr_number=job.pr_number,
        pr_title=job.pr_title,
        base_sha=job.base_sha,
        head_sha=new_head_sha,
        status="RECEIVED",
        version=1,
    )
    session.add(new_job)
    await session.flush()

    job.status = "STALE_SNAPSHOT"
    job.superseded_by_job_id = new_job.id
    await session.commit()

    log_event(
        "job.superseded",
        old_job_id=job.id,
        new_job_id=new_job.id,
        new_head_sha=new_head_sha,
    )
    return new_job
