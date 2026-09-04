"""Periodic stale worker lease recovery service for abandoned execution recovery."""

from datetime import datetime, timezone
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Job
from backend.observability.logging import get_logger
from backend.orchestration.job_service import JobMutationService
from backend.orchestration.state_machine import TERMINAL_STATES

logger = get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def recover_stale_job_leases(
    session: AsyncSession,
    mark_as_failed: bool = True,
) -> List[Job]:
    """Scan and recover jobs whose worker lease has expired without renewal.

    Jobs held by crashed workers are either marked FAILED with an explanatory error,
    or cleared of lease ownership to allow re-claiming.
    """
    now = utcnow()
    stmt = (
        select(Job)
        .where(
            Job.worker_id.isnot(None),
            Job.lease_until.isnot(None),
            Job.lease_until < now,
            Job.status.notin_(list(TERMINAL_STATES)),
        )
    )
    result = await session.execute(stmt)
    stale_jobs = result.scalars().all()

    recovered: List[Job] = []
    for job in stale_jobs:
        logger.warning(
            "Recovering stale job with expired worker lease",
            job_id=job.id,
            worker_id=job.worker_id,
            lease_until=job.lease_until,
            status=job.status,
        )
        if mark_as_failed:
            updated = await JobMutationService.transition(
                session=session,
                job=job,
                new_status="FAILED",
                worker_id=None,
                error_message=f"Execution abandoned: worker lease for {job.worker_id} expired at {job.lease_until}",
                lease_until=None,
            )
            recovered.append(updated)
        else:
            # Release lease back for retry
            job.worker_id = None
            job.lease_until = None
            job.version += 1
            await session.flush()
            recovered.append(job)

    if recovered:
        await session.commit()
        logger.info("Completed stale lease recovery run", recovered_count=len(recovered))

    return recovered
