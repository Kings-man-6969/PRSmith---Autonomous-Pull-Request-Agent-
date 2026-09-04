"""Stale TaskExecutionAttempt recovery service.

Complements backend/orchestration/leases.py (Job-level lease recovery) by
recovering stale TaskExecutionAttempt rows whose worker died without completing.

Guarantee: at-least-once execution with idempotent/reconcilable side effects.

Recovery flow:
    CLAIMED (stale heartbeat) → ABANDONED
    TaskExecution.status → PENDING (if below max_attempts)
    New PENDING attempt created → worker re-claims

This does NOT recover Jobs directly — that is leases.py responsibility.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import TaskExecution, TaskExecutionAttempt
from backend.observability.logging import get_logger, log_event

logger = get_logger(__name__)

MAX_TASK_ATTEMPTS = 5
STALE_HEARTBEAT_THRESHOLD_MINUTES = 10


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def recover_stale_task_attempts(
    session: AsyncSession,
    stale_threshold_minutes: int = STALE_HEARTBEAT_THRESHOLD_MINUTES,
    max_attempts: int = MAX_TASK_ATTEMPTS,
) -> List[TaskExecutionAttempt]:
    """Scan for CLAIMED attempts with stale heartbeats and recover them.

    For each stale attempt:
      1. Mark it ABANDONED.
      2. If the parent TaskExecution has not exceeded max_attempts,
         create a new PENDING attempt and reset TaskExecution to PENDING.
      3. If max_attempts exceeded, mark TaskExecution FAILED.

    Returns list of abandoned attempts processed.
    """
    now = utcnow()
    stale_threshold = now - timedelta(minutes=stale_threshold_minutes)

    stmt = select(TaskExecutionAttempt).where(
        TaskExecutionAttempt.status == "CLAIMED",
        TaskExecutionAttempt.heartbeat_at.isnot(None),
        TaskExecutionAttempt.heartbeat_at < stale_threshold,
    )
    result = await session.execute(stmt)
    stale_attempts: List[TaskExecutionAttempt] = list(result.scalars().all())

    abandoned: List[TaskExecutionAttempt] = []
    for attempt in stale_attempts:
        logger.warning(
            "Abandoning stale task attempt",
            attempt_id=attempt.id,
            task_execution_id=attempt.task_execution_id,
            worker_id=attempt.worker_id,
            heartbeat_at=attempt.heartbeat_at,
        )

        # Mark attempt ABANDONED
        attempt.status = "ABANDONED"
        attempt.completed_at = now
        attempt.error_message = (
            f"Abandoned: worker {attempt.worker_id!r} heartbeat last seen at "
            f"{attempt.heartbeat_at} — exceeded {stale_threshold_minutes}m threshold"
        )
        abandoned.append(attempt)

        # Load parent TaskExecution
        te = await session.get(TaskExecution, attempt.task_execution_id)
        if te is None:
            continue

        # Count all attempts for this TaskExecution
        all_attempts_stmt = select(TaskExecutionAttempt).where(
            TaskExecutionAttempt.task_execution_id == te.id
        )
        all_attempts_res = await session.execute(all_attempts_stmt)
        all_attempts = list(all_attempts_res.scalars().all())
        attempt_count = len(all_attempts)

        if attempt_count >= max_attempts:
            logger.error(
                "TaskExecution exceeded max attempts — marking FAILED",
                task_execution_id=te.id,
                attempts=attempt_count,
            )
            te.status = "FAILED"
        else:
            # Create a new PENDING attempt — a new worker will claim it
            new_attempt = TaskExecutionAttempt(
                task_execution_id=te.id,
                attempt_number=attempt.attempt_number + 1,
                worker_id=None,          # No owner yet — must be claimed
                status="PENDING",        # NOT CLAIMED — ownership requires explicit claim
                claimed_at=None,
                heartbeat_at=None,
            )
            session.add(new_attempt)
            te.status = "PENDING"
            te.current_attempt = attempt.attempt_number + 1
            log_event(
                "task_attempt.recovery_scheduled",
                task_execution_id=te.id,
                new_attempt_number=new_attempt.attempt_number,
            )

    if abandoned:
        await session.commit()
        logger.info("Stale task attempt recovery complete", recovered=len(abandoned))

    return abandoned
