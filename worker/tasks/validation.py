"""Standalone validation Celery task — runs a full 7-layer ValidationPlan on an existing job.

Used for re-validation without a full repair loop (e.g. after manual patch application,
or as a prerequisite check before manual publication).
"""

import asyncio
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.database.models import Job, RepositorySnapshot, ValidationRun, utcnow
from backend.database.sessions import async_session_factory
from backend.observability.logging import get_logger, log_event
from backend.sandbox.docker import DockerBackend
from backend.validation.baseline import BaselineValidator
from backend.validation.runner import ValidationPlan
from worker.celery import celery_app

logger = get_logger(__name__)


async def _async_run_validation(job_id: str, stage: str = "FINAL") -> Dict[str, Any]:
    """Async body: run full 7-layer validation against an existing job's snapshot."""
    async with async_session_factory() as session:
        stmt = (
            select(Job)
            .options(selectinload(Job.repository))
            .where(Job.id == job_id)
        )
        job = (await session.execute(stmt)).scalars().first()
        if not job:
            logger.error("Job not found for validation task", job_id=job_id)
            return {"outcome": "ERROR", "message": "Job not found"}

        if not job.snapshot_id:
            return {"outcome": "ERROR", "message": "Job has no associated snapshot"}

        snap = await session.get(RepositorySnapshot, job.snapshot_id)
        if not snap:
            return {"outcome": "ERROR", "message": "Snapshot not found"}

        from pathlib import Path
        worktree_path = Path(snap.clone_path)

        sandbox = DockerBackend()
        try:
            baseline = await BaselineValidator.capture_baseline(sandbox, worktree_path, job.head_sha)
            commands = BaselineValidator.discover_validation_commands(worktree_path)
            plan = ValidationPlan(
                patch_syntax_check=True,
                lint_command=commands.get("lint"),
                type_check_command=commands.get("type_check"),
                targeted_tests_command=commands.get("targeted_test"),
                full_tests_command=commands.get("full_test"),
            )

            start_ts = utcnow()
            passed, layer_results = await plan.execute(sandbox, worktree_path, baseline)
            duration_ms = int((utcnow() - start_ts).total_seconds() * 1000)

            val_run = ValidationRun(
                job_id=job.id,
                stage=stage,
                passed=passed,
                head_sha=job.head_sha,
                duration_ms=duration_ms,
            )
            session.add(val_run)
            await session.commit()

            log_event("validation.completed", job_id=job.id, stage=stage, passed=passed)
            return {
                "outcome": "PASSED" if passed else "FAILED",
                "stage": stage,
                "validation_run_id": val_run.id,
                "layer_results": layer_results,
            }

        except Exception as e:
            logger.error("Validation task failed", job_id=job_id, error=str(e))
            return {"outcome": "ERROR", "message": str(e)}


@celery_app.task(name="worker.tasks.validation.run_validation", bind=True, max_retries=0)
def run_validation_task(self: Any, job_id: str, stage: str = "FINAL") -> Dict[str, Any]:
    """Celery task for standalone 7-layer validation.

    Used for re-validation without a repair loop, or as a prerequisite check
    before manual publication. max_retries=0 — recovery is application-level.
    """
    logger.info("Executing standalone validation task", job_id=job_id, stage=stage)
    result = asyncio.run(_async_run_validation(job_id, stage))
    logger.info("Validation task complete", job_id=job_id, outcome=result.get("outcome"))
    return result
