"""Transactional Outbox Dispatcher for durable Celery enqueue."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import OutboxEvent, utcnow
from backend.observability.logging import get_logger, log_event

logger = get_logger(__name__)


class OutboxDispatcher:
    """Dispatches OutboxEvents to Celery with atomic claiming and exponential backoff retry."""

    def __init__(self, dispatcher_id: Optional[str] = None, max_attempts: int = 5):
        self.dispatcher_id = dispatcher_id or f"dispatcher-{uuid.uuid4().hex[:8]}"
        self.max_attempts = max_attempts

    async def claim_pending_events(
        self, session: AsyncSession, batch_size: int = 10
    ) -> List[OutboxEvent]:
        """Atomically claim up to batch_size pending outbox events for this dispatcher."""
        now = utcnow()
        candidate_stmt = (
            select(OutboxEvent.id)
            .where(
                OutboxEvent.status == "PENDING",
                or_(
                    OutboxEvent.next_attempt_at.is_(None),
                    OutboxEvent.next_attempt_at <= now,
                ),
            )
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
        )
        res = await session.execute(candidate_stmt)
        candidate_ids = res.scalars().all()

        claimed_events: List[OutboxEvent] = []
        for event_id in candidate_ids:
            claim_stmt = (
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.status == "PENDING",
                )
                .values(
                    status="CLAIMED",
                    claimed_by=self.dispatcher_id,
                    claimed_at=now,
                    attempts=OutboxEvent.attempts + 1,
                )
                .returning(OutboxEvent)
            )
            claim_res = await session.execute(claim_stmt)
            claimed = claim_res.scalars().first()
            if claimed:
                claimed_events.append(claimed)

        await session.commit()
        return claimed_events

    async def dispatch_single_event(
        self, session: AsyncSession, event: OutboxEvent
    ) -> bool:
        """Route and enqueue a single event to Celery, updating status on success/failure."""
        now = utcnow()
        try:
            # Route event to task
            if event.event_type == "review.dispatch":
                from worker.tasks.review import process_pr_job

                process_pr_job.delay(event.aggregate_id)
            elif event.event_type == "graph.build":
                from worker.tasks.graph import build_repository_graph

                build_repository_graph.delay(event.aggregate_id)
            else:
                logger.warning(
                    "Unknown outbox event type encountered",
                    event_type=event.event_type,
                    event_id=event.id,
                )

            # Mark as successfully dispatched
            event.status = "DISPATCHED"
            event.processed_at = now
            await session.commit()
            log_event(
                "outbox.dispatched",
                event_id=event.id,
                event_type=event.event_type,
                aggregate_id=event.aggregate_id,
            )
            return True

        except Exception as exc:
            logger.error(
                "Failed to dispatch outbox event to Celery",
                event_id=event.id,
                error=str(exc),
                attempts=event.attempts,
            )
            if event.attempts >= self.max_attempts:
                event.status = "FAILED"
                event.processed_at = now
            else:
                # Exponential backoff: 15s, 30s, 60s, 120s...
                backoff_seconds = 15 * (2 ** (event.attempts - 1))
                event.status = "PENDING"
                event.claimed_by = None
                event.claimed_at = None
                event.next_attempt_at = now + timedelta(seconds=backoff_seconds)

            await session.commit()
            return False

    async def dispatch_pending(
        self, session: AsyncSession, batch_size: int = 10
    ) -> int:
        """Claim and dispatch pending events. Returns count of dispatched events."""
        events = await self.claim_pending_events(session, batch_size=batch_size)
        dispatched_count = 0
        for event in events:
            success = await self.dispatch_single_event(session, event)
            if success:
                dispatched_count += 1
        return dispatched_count

    async def recover_stale_claimed(
        self, session: AsyncSession, timeout_seconds: int = 300
    ) -> int:
        """Recover events that were claimed by a worker or dispatcher that crashed."""
        now = utcnow()
        stale_threshold = now - timedelta(seconds=timeout_seconds)

        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status == "CLAIMED",
                OutboxEvent.claimed_at <= stale_threshold,
            )
        )
        res = await session.execute(stmt)
        stale_events = res.scalars().all()

        recovered_count = 0
        for event in stale_events:
            backoff_seconds = 15 * (2 ** max(0, event.attempts - 1))
            event.status = "PENDING"
            event.claimed_by = None
            event.claimed_at = None
            event.next_attempt_at = now + timedelta(seconds=backoff_seconds)
            recovered_count += 1

        if recovered_count > 0:
            await session.commit()
            logger.info("Recovered stale outbox events", count=recovered_count)

        return recovered_count

    recover_stale_dispatches = recover_stale_claimed
