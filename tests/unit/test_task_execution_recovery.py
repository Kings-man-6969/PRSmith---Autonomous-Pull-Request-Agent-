"""Failure injection test: TaskExecution worker abandoned lease recovery and audit trail."""

from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import TaskExecution, TaskExecutionAttempt, utcnow
from backend.delivery.task_executions import (
    claim_task_execution,
    complete_task_execution,
    recover_abandoned_attempts,
)


@pytest.mark.asyncio
async def test_worker_crashes_and_recovers_under_same_execution_identity(db_session: AsyncSession):
    """Worker 1 dies after claiming; recovery marks attempt ABANDONED and worker 2 completes attempt 2."""
    idempotency_key = "task:review:repo1:pr10:head123:v2"

    # Step 1: Worker 1 claims task execution (Attempt 1)
    attempt1 = await claim_task_execution(
        session=db_session,
        idempotency_key=idempotency_key,
        task_name="review.dispatch",
        worker_id="worker-1",
    )
    assert attempt1 is not None
    assert attempt1.attempt_number == 1
    assert attempt1.status == "CLAIMED"
    execution_id = attempt1.task_execution_id

    # Step 2: Worker 1 dies; its heartbeat stops in the past
    past_time = utcnow() - timedelta(minutes=15)
    attempt1.heartbeat_at = past_time
    await db_session.commit()

    # Step 3: Beat scheduler runs recover_abandoned_attempts (with 300s timeout)
    recovered = await recover_abandoned_attempts(session=db_session, timeout_seconds=300)
    assert len(recovered) == 1
    assert recovered[0] == idempotency_key
    await db_session.refresh(attempt1)
    assert attempt1.status == "ABANDONED"

    # Step 4: Worker 2 receives or retries task for SAME idempotency_key
    attempt2 = await claim_task_execution(
        session=db_session,
        idempotency_key=idempotency_key,
        task_name="review.dispatch",
        worker_id="worker-2",
    )
    # Crucial invariants:
    # 1. New attempt created
    # 2. Bound to the EXACT same TaskExecution ID
    # 3. Attempt number incremented to 2
    assert attempt2 is not None
    assert attempt2.task_execution_id == execution_id
    assert attempt2.attempt_number == 2
    assert attempt2.worker_id == "worker-2"
    assert attempt2.status == "CLAIMED"

    # Step 5: Worker 2 completes successfully
    await complete_task_execution(
        session=db_session,
        attempt=attempt2,
    )

    # Step 6: Verify full audit trail and parent status
    parent = await db_session.scalar(
        select(TaskExecution).where(TaskExecution.id == execution_id)
    )
    assert parent is not None
    assert parent.status == "COMPLETED"

    attempts = (
        await db_session.scalars(
            select(TaskExecutionAttempt)
            .where(TaskExecutionAttempt.task_execution_id == execution_id)
            .order_by(TaskExecutionAttempt.attempt_number)
        )
    ).all()

    assert len(attempts) == 2
    assert attempts[0].attempt_number == 1
    assert attempts[0].worker_id == "worker-1"
    assert attempts[0].status == "ABANDONED"

    assert attempts[1].attempt_number == 2
    assert attempts[1].worker_id == "worker-2"
    assert attempts[1].status == "COMPLETED"
