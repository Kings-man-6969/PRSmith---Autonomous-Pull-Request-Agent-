"""Unit tests for Webhook Outbox and TaskExecution reliability."""

import uuid
from datetime import timedelta
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from backend.app import app
from backend.database.models import Job, OutboxEvent, TaskExecution, WebhookDelivery, utcnow
from backend.database.sessions import get_db_session
from backend.delivery.outbox import OutboxDispatcher
from backend.delivery.task_executions import (
    claim_task_execution,
    complete_task_execution,
    heartbeat_task_execution,
    recover_abandoned_attempts,
)


@pytest.mark.asyncio
async def test_webhook_atomic_dedup_and_outbox_enqueue():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        delivery_id = f"test-deliv-{uuid.uuid4().hex}"
        payload = {
            "action": "opened",
            "repository": {"id": 99991, "full_name": "owner/test-repo", "name": "test-repo", "owner": {"login": "owner"}},
            "pull_request": {"number": 42, "title": "Add feature", "head": {"sha": "head111"}, "base": {"sha": "base000"}},
        }
        headers = {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": delivery_id,
        }

        # First request: should create delivery, job, outbox
        resp1 = await client.post("/webhook", json=payload, headers=headers)
        assert resp1.status_code == 202
        data1 = resp1.json()
        assert data1["status"] == "enqueued"
        job_id = data1["job_id"]

        # Verify DB records
        async for session in get_db_session():
            job = await session.scalar(select(Job).where(Job.id == job_id))
            assert job is not None
            assert job.pr_number == 42
            assert job.version == 1

            outbox = await session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id))
            assert outbox is not None
            assert outbox.event_type == "review.dispatch"
            assert outbox.status == "PENDING"
            assert outbox.attempts == 0

        # Second request with SAME delivery_id: should be ignored duplicate
        resp2 = await client.post("/webhook", json=payload, headers=headers)
        assert resp2.status_code == 202
        data2 = resp2.json()
        assert data2["status"] == "ignored_duplicate"
        assert data2["delivery_id"] == delivery_id


@pytest.mark.asyncio
async def test_outbox_dispatcher_claim_and_recovery():
    async for session in get_db_session():
        job_id = f"job-{uuid.uuid4().hex}"
        event = OutboxEvent(
            id=str(uuid.uuid4()),
            event_type="test.event",
            aggregate_id=job_id,
            status="PENDING",
            attempts=0,
        )
        session.add(event)
        await session.commit()

        dispatcher = OutboxDispatcher(dispatcher_id="test-worker-1")
        claimed = await dispatcher.claim_pending_events(session, batch_size=10)
        assert any(e.id == event.id for e in claimed)

        # Check status is now CLAIMED
        refreshed = await session.scalar(select(OutboxEvent).where(OutboxEvent.id == event.id))
        assert refreshed.status == "CLAIMED"
        assert refreshed.claimed_by == "test-worker-1"
        assert refreshed.attempts == 1

        # Simulate crashed worker: backdate claimed_at
        refreshed.claimed_at = utcnow() - timedelta(seconds=400)
        await session.commit()

        # Run recovery
        recovered_count = await dispatcher.recover_stale_claimed(session, timeout_seconds=300)
        assert recovered_count >= 1

        refreshed_after_recovery = await session.scalar(select(OutboxEvent).where(OutboxEvent.id == event.id))
        assert refreshed_after_recovery.status == "PENDING"
        assert refreshed_after_recovery.claimed_by is None


@pytest.mark.asyncio
async def test_task_execution_lifecycle_and_abandon_recovery():
    async for session in get_db_session():
        key = f"task:test:{uuid.uuid4().hex}"

        # Worker 1 claims task
        attempt1 = await claim_task_execution(
            session=session,
            idempotency_key=key,
            task_name="test_task",
            worker_id="worker-1",
        )
        assert attempt1 is not None
        assert attempt1.attempt_number == 1
        assert attempt1.status == "CLAIMED"

        # Worker 2 tries to claim same key concurrently (active fresh attempt)
        attempt2 = await claim_task_execution(
            session=session,
            idempotency_key=key,
            task_name="test_task",
            worker_id="worker-2",
        )
        assert attempt2 is None  # Blocked, already claimed

        # Heartbeat update
        beat_ok = await heartbeat_task_execution(session, attempt1.id)
        assert beat_ok is True

        # Simulate worker 1 crashing: backdate heartbeat_at
        attempt1.heartbeat_at = utcnow() - timedelta(seconds=600)
        await session.commit()

        # Sweeper marks abandoned
        abandoned_keys = await recover_abandoned_attempts(session, timeout_seconds=300)
        assert key in abandoned_keys

        # Worker 2 now attempts again: should get attempt #2
        attempt_retry = await claim_task_execution(
            session=session,
            idempotency_key=key,
            task_name="test_task",
            worker_id="worker-2",
        )
        assert attempt_retry is not None
        assert attempt_retry.attempt_number == 2
        assert attempt_retry.worker_id == "worker-2"

        # Complete attempt #2
        await complete_task_execution(session, attempt_retry.id, success=True)

        exec_row = await session.scalar(select(TaskExecution).where(TaskExecution.idempotency_key == key))
        assert exec_row.status == "COMPLETED"
        assert exec_row.current_attempt == 2
