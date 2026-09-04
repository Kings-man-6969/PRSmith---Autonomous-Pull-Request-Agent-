"""Task execution ownership, attempt tracking, and heartbeat recovery."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import TaskExecution, TaskExecutionAttempt, utcnow
from backend.observability.logging import get_logger, log_event

logger = get_logger(__name__)


async def claim_task_execution(
    session: AsyncSession,
    idempotency_key: str,
    task_name: str,
    worker_id: str,
    stale_timeout_seconds: int = 300,
) -> Optional[TaskExecutionAttempt]:
    """Attempt to claim ownership of a logical task execution for this worker.

    Returns:
        TaskExecutionAttempt if ownership was successfully acquired, or None if
        another worker actively owns the task or the task is already completed.
    """
    now = utcnow()

    # Step 1: Check if TaskExecution already exists
    stmt = select(TaskExecution).where(TaskExecution.idempotency_key == idempotency_key)
    res = await session.execute(stmt)
    execution = res.scalars().first()

    if not execution:
        # Create novel execution and attempt #1
        try:
            execution = TaskExecution(
                idempotency_key=idempotency_key,
                task_name=task_name,
                status="IN_PROGRESS",
                current_attempt=1,
            )
            session.add(execution)
            await session.flush()

            attempt = TaskExecutionAttempt(
                task_execution_id=execution.id,
                attempt_number=1,
                worker_id=worker_id,
                claimed_at=now,
                heartbeat_at=now,
                status="CLAIMED",
            )
            session.add(attempt)
            await session.commit()
            log_event(
                "task_execution.claimed_initial",
                key=idempotency_key,
                worker_id=worker_id,
                attempt=1,
            )
            return attempt
        except IntegrityError:
            # Race condition: another worker inserted it concurrently
            await session.rollback()
            res = await session.execute(stmt)
            execution = res.scalars().first()
            if not execution:
                return None

    # Step 2: If execution already completed, no work needed
    if execution.status == "COMPLETED":
        logger.info(
            "Task execution already completed — ignoring duplicate",
            key=idempotency_key,
        )
        return None

    # Step 3: Check current active attempt status
    attempt_stmt = (
        select(TaskExecutionAttempt)
        .where(TaskExecutionAttempt.task_execution_id == execution.id)
        .order_by(TaskExecutionAttempt.attempt_number.desc())
        .limit(1)
    )
    attempt_res = await session.execute(attempt_stmt)
    latest_attempt = attempt_res.scalars().first()

    if latest_attempt and latest_attempt.status == "CLAIMED":
        # Check if heartbeat is still fresh
        if latest_attempt.heartbeat_at > now - timedelta(seconds=stale_timeout_seconds):
            # Another worker is actively executing this attempt
            return None

        # Stale attempt: mark as abandoned and start new attempt
        latest_attempt.status = "ABANDONED"
        latest_attempt.error_message = f"Abandoned due to heartbeat timeout (> {stale_timeout_seconds}s)"

    # Create next attempt
    next_attempt_num = (latest_attempt.attempt_number + 1) if latest_attempt else 1
    execution.status = "IN_PROGRESS"
    execution.current_attempt = next_attempt_num

    new_attempt = TaskExecutionAttempt(
        task_execution_id=execution.id,
        attempt_number=next_attempt_num,
        worker_id=worker_id,
        claimed_at=now,
        heartbeat_at=now,
        status="CLAIMED",
    )
    session.add(new_attempt)
    await session.commit()
    log_event(
        "task_execution.claimed_retry",
        key=idempotency_key,
        worker_id=worker_id,
        attempt=next_attempt_num,
    )
    return new_attempt


async def heartbeat_task_execution(
    session: AsyncSession, attempt_id: str
) -> bool:
    """Update heartbeat timestamp for an active execution attempt."""
    now = utcnow()
    stmt = (
        update(TaskExecutionAttempt)
        .where(
            TaskExecutionAttempt.id == attempt_id,
            TaskExecutionAttempt.status == "CLAIMED",
        )
        .values(heartbeat_at=now)
    )
    res = await session.execute(stmt)
    await session.commit()
    return (res.rowcount or 0) > 0


async def complete_task_execution(
    session: AsyncSession,
    attempt_id: Optional[str] = None,
    attempt: Optional[TaskExecutionAttempt] = None,
    success: bool = True,
    error_message: Optional[str] = None,
) -> None:
    """Mark execution attempt and parent task execution as COMPLETED or FAILED."""
    now = utcnow()
    final_status = "COMPLETED" if success else "FAILED"
    resolved_attempt_id = attempt.id if attempt is not None else attempt_id
    if not resolved_attempt_id:
        raise ValueError("Either attempt_id or attempt must be provided")

    attempt_stmt = select(TaskExecutionAttempt).where(TaskExecutionAttempt.id == resolved_attempt_id)
    attempt_res = await session.execute(attempt_stmt)
    target_attempt = attempt_res.scalars().first()

    if target_attempt:
        target_attempt.status = final_status
        target_attempt.completed_at = now
        target_attempt.error_message = error_message

        exec_stmt = select(TaskExecution).where(TaskExecution.id == target_attempt.task_execution_id)
        exec_res = await session.execute(exec_stmt)
        execution = exec_res.scalars().first()
        if execution:
            execution.status = final_status

        await session.commit()
        log_event(
            "task_execution.completed",
            attempt_id=resolved_attempt_id,
            status=final_status,
        )


async def recover_abandoned_attempts(
    session: AsyncSession, timeout_seconds: int = 300
) -> List[str]:
    """Identify stale attempts, mark as ABANDONED, and return affected idempotency keys."""
    now = utcnow()
    stale_threshold = now - timedelta(seconds=timeout_seconds)

    stmt = (
        select(TaskExecutionAttempt, TaskExecution.idempotency_key)
        .join(TaskExecution, TaskExecutionAttempt.task_execution_id == TaskExecution.id)
        .where(
            TaskExecutionAttempt.status == "CLAIMED",
            TaskExecutionAttempt.heartbeat_at <= stale_threshold,
        )
    )
    res = await session.execute(stmt)
    rows = res.all()

    recovered_keys: List[str] = []
    for attempt, key in rows:
        attempt.status = "ABANDONED"
        attempt.error_message = f"Marked abandoned by recovery sweeper (stale > {timeout_seconds}s)"
        recovered_keys.append(key)

    if recovered_keys:
        await session.commit()
        logger.info(
            "Marked stale task attempts as ABANDONED",
            count=len(recovered_keys),
            keys=recovered_keys,
        )

    return recovered_keys
