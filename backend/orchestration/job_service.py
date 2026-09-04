"""Authoritative mutation service for all Job lifecycle state changes and concurrency control."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Job
from backend.observability.logging import get_logger
from backend.orchestration.state_machine import (
    LeaseConflictError,
    StaleJobError,
    validate_transition,
)
from backend.review.snapshot_service import mark_superseded as _snapshot_mark_superseded

logger = get_logger(__name__)


def utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class JobMutationService:
    """Single authoritative interface for all Job state changes, enforcing:
    1. Versioned optimistic concurrency control (Invariant 11).
    2. Formal state machine transition validation.
    3. Strict distributed lease ownership per worker.
    """

    @staticmethod
    async def transition(
        session: AsyncSession,
        job: Job,
        new_status: str,
        worker_id: Optional[str] = None,
        **fields: Any,
    ) -> Job:
        """Atomically transition a Job to a new status with optimistic version check.

        Raises:
            LeaseConflictError: If a different worker holds an unexpired lease on this job.
            InvalidStateTransitionError: If the transition graph prohibits this path.
            StaleJobError: If the job version was modified concurrently.
        """
        now = utcnow()

        # Check lease ownership if worker_id is specified
        if worker_id and job.worker_id and job.worker_id != worker_id:
            lease_active = job.lease_until and (
                job.lease_until.replace(tzinfo=timezone.utc)
                if job.lease_until.tzinfo is None
                else job.lease_until
            ) > now
            if lease_active:
                raise LeaseConflictError(
                    f"Job {job.id} is leased to worker '{job.worker_id}' until {job.lease_until}"
                )

        # Validate against the formal state machine
        validate_transition(job.status, new_status)

        current_version = job.version
        update_values: Dict[str, Any] = {
            "status": new_status,
            "version": current_version + 1,
            "updated_at": now,
            **fields,
        }
        if worker_id:
            update_values["worker_id"] = worker_id

        stmt = (
            update(Job)
            .where(Job.id == job.id, Job.version == current_version)
            .values(**update_values)
        )
        result = await session.execute(stmt)

        if result.rowcount == 0:
            raise StaleJobError(
                f"Job {job.id} concurrency conflict: expected version {current_version} was modified"
            )

        # Update local in-memory instance
        job.version = current_version + 1
        job.status = new_status
        job.updated_at = now
        for k, v in fields.items():
            setattr(job, k, v)
        if worker_id:
            job.worker_id = worker_id

        await session.flush()
        logger.info(
            "Job status transitioned",
            job_id=job.id,
            old_status=job.status,
            new_status=new_status,
            version=job.version,
        )
        return job

    @staticmethod
    async def acquire_lease(
        session: AsyncSession,
        job: Job,
        worker_id: str,
        duration_s: int = 300,
    ) -> Job:
        """Acquire an exclusive worker execution lease on the Job.

        Raises:
            LeaseConflictError: If another worker currently holds an unexpired lease.
            StaleJobError: If version conflict occurs during acquisition.
        """
        now = utcnow()
        if job.worker_id and job.worker_id != worker_id:
            lease_active = job.lease_until and (
                job.lease_until.replace(tzinfo=timezone.utc)
                if job.lease_until.tzinfo is None
                else job.lease_until
            ) > now
            if lease_active:
                raise LeaseConflictError(
                    f"Cannot acquire lease: Job {job.id} is held by {job.worker_id} until {job.lease_until}"
                )

        lease_until = now + timedelta(seconds=duration_s)
        current_version = job.version

        stmt = (
            update(Job)
            .where(Job.id == job.id, Job.version == current_version)
            .values(
                worker_id=worker_id,
                lease_until=lease_until,
                version=current_version + 1,
                updated_at=now,
            )
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            raise StaleJobError(f"Job {job.id}: lease acquisition failed due to version mismatch")

        job.worker_id = worker_id
        job.lease_until = lease_until
        job.version = current_version + 1
        job.updated_at = now
        await session.flush()
        return job

    @staticmethod
    async def renew_lease(
        session: AsyncSession,
        job: Job,
        worker_id: str,
        extension_s: int = 300,
    ) -> bool:
        """Renew an existing worker lease."""
        if job.worker_id != worker_id:
            raise LeaseConflictError(
                f"Worker {worker_id} cannot renew lease held by {job.worker_id}"
            )

        now = utcnow()
        lease_until = now + timedelta(seconds=extension_s)
        current_version = job.version

        stmt = (
            update(Job)
            .where(Job.id == job.id, Job.version == current_version, Job.worker_id == worker_id)
            .values(
                lease_until=lease_until,
                version=current_version + 1,
                updated_at=now,
            )
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            return False

        job.lease_until = lease_until
        job.version = current_version + 1
        job.updated_at = now
        await session.flush()
        return True

    @staticmethod
    async def release_lease(
        session: AsyncSession,
        job: Job,
        worker_id: str,
    ) -> None:
        """Release worker lease upon task completion or termination."""
        if job.worker_id and job.worker_id != worker_id:
            raise LeaseConflictError(
                f"Worker {worker_id} cannot release lease held by {job.worker_id}"
            )

        now = utcnow()
        current_version = job.version
        stmt = (
            update(Job)
            .where(Job.id == job.id, Job.version == current_version)
            .values(
                worker_id=None,
                lease_until=None,
                version=current_version + 1,
                updated_at=now,
            )
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            raise StaleJobError(f"Job {job.id}: lease release failed due to version mismatch")

        job.worker_id = None
        job.lease_until = None
        job.version = current_version + 1
        job.updated_at = now
        await session.flush()

    @staticmethod
    async def mark_superseded(
        session: AsyncSession,
        job: Job,
        new_head_sha: str,
    ) -> Job:
        """Mark a job SUPERSEDED and create an immutable successor job."""
        return await _snapshot_mark_superseded(
            session=session,
            job=job,
            new_head_sha=new_head_sha,
        )
