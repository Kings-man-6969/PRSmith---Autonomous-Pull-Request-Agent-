"""Failure injection test: Outbox dispatcher crash before marking DISPATCHED."""

from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import OutboxEvent, TaskExecution, utcnow
from backend.delivery.outbox import OutboxDispatcher
from backend.delivery.task_executions import claim_task_execution, complete_task_execution


@pytest.mark.asyncio
async def test_dispatcher_crash_recovery_and_worker_idempotent_dedup(db_session: AsyncSession):
    """Simulate dispatcher crash after enqueuing task; recovery re-dispatches, and TaskExecution suppresses duplicate."""
    # 1. Create pending OutboxEvent
    event = OutboxEvent(
        event_type="review.dispatch",
        aggregate_id="job-crash-1",
        payload={"job_id": "job-crash-1", "pr_number": 99},
        status="PENDING",
    )
    db_session.add(event)
    await db_session.commit()
    await db_session.refresh(event)

    # 2. Dispatcher A claims the event
    dispatcher_a = OutboxDispatcher(dispatcher_id="dispatcher-A")
    claimed = await dispatcher_a.claim_pending_events(
        session=db_session,
        batch_size=10,
    )
    assert len(claimed) == 1
    assert claimed[0].status == "CLAIMED"
    assert claimed[0].claimed_by == "dispatcher-A"

    # 3. Simulate CRASH of Dispatcher A before marking DISPATCHED!
    # Claimed time ages past the recovery threshold
    past_claimed = utcnow() - timedelta(minutes=10)
    event.claimed_at = past_claimed
    await db_session.commit()

    # 4. Dispatcher B starts up, detects abandoned event, and resets it to PENDING
    dispatcher_b = OutboxDispatcher(dispatcher_id="dispatcher-B")
    reset_count = await dispatcher_b.recover_stale_dispatches(
        session=db_session,
        timeout_seconds=300,
    )
    assert reset_count == 1
    await db_session.refresh(event)
    assert event.status == "PENDING"
    assert event.claimed_by is None

    # 5. Worker execution idempotency check:
    # First enqueue execution arrives at worker
    task_key = "task:job:job-crash-1:pipeline-v2"
    attempt1 = await claim_task_execution(
        session=db_session,
        idempotency_key=task_key,
        task_name="review.dispatch",
        worker_id="worker-node-1",
    )
    assert attempt1 is not None
    await complete_task_execution(session=db_session, attempt=attempt1)

    # Re-dispatched duplicate task arrives at worker
    attempt2 = await claim_task_execution(
        session=db_session,
        idempotency_key=task_key,
        task_name="review.dispatch",
        worker_id="worker-node-2",
    )
    # Crucial invariant: Duplicate is detected and suppressed!
    assert attempt2 is None
