"""Failure injection test: Redis / Celery broker outage resilience.

Verifies:
1. DB commit of the webhook / job succeeds and OutboxEvent remains PENDING.
2. During Redis outage, dispatch attempt fails gracefully and resets status to PENDING with exponential backoff.
3. Upon Redis recovery, the dispatcher successfully routes the task to Celery and marks status as DISPATCHED.
"""

import uuid
from unittest.mock import MagicMock, patch
import pytest

from backend.database.models import Job, OutboxEvent, Repository
from backend.database.sessions import async_session_factory
from backend.delivery.outbox import OutboxDispatcher


@pytest.mark.asyncio
async def test_redis_failure_and_recovery():
    async with async_session_factory() as session:
        unique_suffix = uuid.uuid4().hex[:8]
        repo = Repository(
            id=str(uuid.uuid4()),
            github_id=int(uuid.uuid4().int % 1000000),
            full_name=f"acme/redis-outage-{unique_suffix}",
            owner="acme",
            name=f"redis-outage-{unique_suffix}",
        )
        session.add(repo)
        await session.flush()

        job = Job(
            id=str(uuid.uuid4()),
            repository_id=repo.id,
            pr_number=55,
            base_sha="base",
            head_sha="head",
            status="RECEIVED",
        )
        outbox_event = OutboxEvent(
            id=str(uuid.uuid4()),
            event_type="review.dispatch",
            aggregate_id=job.id,
            status="PENDING",
            payload={"pr_number": 55},
        )
        session.add_all([job, outbox_event])
        await session.commit()

        dispatcher = OutboxDispatcher()

        # 1. Simulate Redis outage when attempting to dispatch to Celery
        mock_task = MagicMock()
        mock_task.delay.side_effect = ConnectionError("Redis connection refused")
        mock_module = MagicMock(process_pr_job=mock_task)

        with patch.dict("sys.modules", {"worker.tasks.review": mock_module}):
            claimed_events = await dispatcher.claim_pending_events(session, batch_size=1)
            assert len(claimed_events) == 1
            event = claimed_events[0]
            success = await dispatcher.dispatch_single_event(session, event)
            assert success is False

        # Invariant: event was not lost; status reset to PENDING with backoff
        await session.refresh(outbox_event)
        assert outbox_event.status == "PENDING"
        assert outbox_event.attempts == 1
        assert outbox_event.next_attempt_at is not None

        # 2. Simulate Redis recovery: reset next_attempt_at to allow immediate dispatch
        outbox_event.next_attempt_at = None
        await session.commit()

        mock_recovered_task = MagicMock()
        mock_recovered_module = MagicMock(process_pr_job=mock_recovered_task)

        with patch.dict("sys.modules", {"worker.tasks.review": mock_recovered_module}):
            claimed_events = await dispatcher.claim_pending_events(session, batch_size=1)
            assert len(claimed_events) == 1
            recovered_event = claimed_events[0]
            success = await dispatcher.dispatch_single_event(session, recovered_event)
            assert success is True

        # Invariant: task dispatched to Celery, OutboxEvent marked DISPATCHED
        mock_recovered_task.delay.assert_called_once_with(job.id)
        await session.refresh(outbox_event)
        assert outbox_event.status == "DISPATCHED"
        assert outbox_event.processed_at is not None
