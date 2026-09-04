"""Repair Celery tasks — orchestration only. Domain logic lives in backend.repair.service."""

import asyncio
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.database.models import Job
from backend.database.sessions import async_session_factory
from backend.observability.logging import get_logger
from backend.repair.service import RepairService
from worker.celery import celery_app

logger = get_logger(__name__)


async def _async_repair_finding(job_id: str, finding_id: str) -> Dict[str, Any]:
    """Async body for single-finding manual repair (dashboard-triggered)."""
    async with async_session_factory() as session:
        stmt = (
            select(Job)
            .options(selectinload(Job.repository))
            .where(Job.id == job_id)
        )
        job = (await session.execute(stmt)).scalars().first()
        if not job:
            logger.error("Job not found for repair task", job_id=job_id)
            return {"outcome": "ERROR", "message": "Job not found"}

        return await RepairService.execute_finding_repair_by_id(
            session=session,
            job=job,
            finding_orm_id=finding_id,
        )


@celery_app.task(name="worker.tasks.repair.repair_finding", bind=True, max_retries=0)
def repair_finding_task(self: Any, job_id: str, finding_id: str) -> Dict[str, Any]:
    """Celery task for dashboard-triggered single-finding repair.

    max_retries=0: at-least-once delivery is handled at the application level
    via the RepairRun record — not by Celery auto-retry.
    """
    logger.info("Executing repair task", job_id=job_id, finding_id=finding_id)
    result = asyncio.run(_async_repair_finding(job_id, finding_id))
    logger.info("Repair task complete", job_id=job_id, finding_id=finding_id, outcome=result.get("outcome"))
    return result
