"""Repair domain service — centralised repair logic callable by all worker tasks.

This module owns all repair domain logic. Worker tasks (repair.py, review.py)
orchestrate; this service implements. This prevents circular imports between
worker modules and keeps domain logic testable independently.

Architecture:
    backend/repair/service.py        ← domain logic (here)
            ↑
    worker/tasks/repair.py           ← Celery task (calls service)
    worker/tasks/review.py           ← Celery task (calls service for auto-repair)
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import (
    GraphVersion,
    Job,
    Patch,
    RepairRun,
    RepositorySnapshot,
    ReviewFinding as ReviewFindingORM,
    utcnow,
)
from backend.observability.logging import get_logger, log_event
from backend.orchestration.hashing import canonical_hash
from backend.repair.loop import RepairLoopOrchestrator
from backend.review.schemas import ReviewFinding as ReviewFindingSchema
from backend.sandbox.docker import DockerBackend
from backend.validation.runner import ValidationPlan

logger = get_logger(__name__)


class RepairService:
    """Domain service for executing finding-level repair operations."""

    @staticmethod
    def make_orchestrator(sandbox: Optional[DockerBackend] = None) -> RepairLoopOrchestrator:
        """Factory for the repair loop orchestrator."""
        return RepairLoopOrchestrator(sandbox or DockerBackend())

    @staticmethod
    async def execute_finding_repair(
        session: AsyncSession,
        job: Job,
        snapshot: RepositorySnapshot,
        graph_version: GraphVersion,
        pydantic_finding: ReviewFindingSchema,
        worktree_path: Path,
        baseline: Any,
        validation_plan: ValidationPlan,
        repair_orchestrator_factory: Optional[Callable[[], RepairLoopOrchestrator]] = None,
    ) -> Dict[str, Any]:
        """Execute isolated repair for a single finding.

        Creates a RepairRun record, delegates to RepairLoopOrchestrator,
        persists the patch on success, and updates the finding's status.

        Returns:
            dict with keys: outcome (SUCCESS|FAILED|ESCALATED), iterations, patch_diff
        """
        orchestrator = repair_orchestrator_factory() if repair_orchestrator_factory else RepairService.make_orchestrator()

        repair_run = RepairRun(
            job_id=job.id,
            finding_id=pydantic_finding.id,
            status="STARTED",
        )
        session.add(repair_run)
        await session.flush()

        log_event(
            "repair.started",
            job_id=job.id,
            finding_id=pydantic_finding.id,
            repairability=pydantic_finding.repairability,
        )

        try:
            result = await orchestrator.execute_repair(
                session=session,
                snapshot=snapshot,
                graph_version=graph_version,
                worktree_path=worktree_path,
                finding=pydantic_finding,
                baseline=baseline,
                validation_plan=validation_plan,
            )
        except Exception as e:
            logger.error(
                "Repair execution raised unexpected error",
                job_id=job.id,
                finding_id=pydantic_finding.id,
                error=str(e),
            )
            repair_run.status = "FAILED"
            repair_run.completed_at = utcnow()
            await session.commit()
            return {"outcome": "FAILED", "iterations": 0, "patch_diff": ""}

        outcome = result.get("outcome", "FAILED")
        repair_run.status = outcome
        repair_run.iteration_count = result.get("iterations", 0)
        repair_run.completed_at = utcnow()

        patch_diff = result.get("patch_diff", "")
        if outcome == "SUCCESS" and patch_diff:
            # Compute canonical artifact hash for this patch
            artifact_hash = canonical_hash(patch_diff)

            patch_record = Patch(
                job_id=job.id,
                diff_content=patch_diff,
                diff_hash=canonical_hash(patch_diff),
                artifact_hash=artifact_hash,
                is_valid_syntax=True,
                is_within_scope=True,
            )
            session.add(patch_record)

            # Update the finding's lifecycle status
            finding_stmt = select(ReviewFindingORM).where(ReviewFindingORM.finding_id == pydantic_finding.id)
            db_finding = (await session.execute(finding_stmt)).scalars().first()
            if db_finding:
                db_finding.status = "REPAIRED"

        await session.commit()
        log_event("repair.completed", job_id=job.id, finding_id=pydantic_finding.id, outcome=outcome)
        return {"outcome": outcome, "iterations": result.get("iterations", 0), "patch_diff": patch_diff}

    @staticmethod
    async def execute_finding_repair_by_id(
        session: AsyncSession,
        job: Job,
        finding_orm_id: str,
        worktree_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Convenience: load a finding by ORM ID and execute repair.

        Used by worker/tasks/repair.py for dashboard-triggered manual repairs.
        """
        from backend.repository.worktree import WorktreeManager
        from backend.validation.baseline import BaselineValidator
        from backend.sandbox.docker import DockerBackend

        # Load the finding
        stmt = (
            select(ReviewFindingORM)
            .where(ReviewFindingORM.id == finding_orm_id)
        )
        db_finding = (await session.execute(stmt)).scalars().first()
        if not db_finding:
            return {"outcome": "ERROR", "message": "Finding not found"}

        # Load snapshot and graph version
        snap_stmt = (
            select(RepositorySnapshot)
            .where(RepositorySnapshot.id == job.snapshot_id)
        )
        snapshot = (await session.execute(snap_stmt)).scalars().first()
        if not snapshot:
            return {"outcome": "ERROR", "message": "Snapshot not found"}

        gv_stmt = (
            select(GraphVersion)
            .where(GraphVersion.id == job.graph_version_id)
        )
        graph_version = (await session.execute(gv_stmt)).scalars().first()
        if not graph_version:
            return {"outcome": "ERROR", "message": "GraphVersion not found"}

        # Adapt ORM finding to Pydantic schema
        from backend.review.schemas import ReviewFinding as ReviewFindingSchema, Evidence as EvidenceSchema
        evidence_list = [
            EvidenceSchema(
                entity_type=e.get("entity_type", "Function"),
                entity_name=e.get("entity_name", ""),
                file_path=e.get("file_path", ""),
                relationship=e.get("relationship", "calls"),
                verified_in_graph=e.get("verified_in_graph", False),
                snippet=e.get("snippet"),
                line_number=e.get("line_number"),
            )
            for e in (db_finding.evidence or [])
        ]
        pydantic_finding = ReviewFindingSchema(
            id=db_finding.finding_id,
            severity=db_finding.severity,
            category=db_finding.category,
            file=db_finding.file_path,
            symbol=db_finding.symbol_name,
            lines=[db_finding.start_line, db_finding.end_line] if db_finding.start_line else None,
            description=db_finding.description,
            evidence=evidence_list,
            affected_entities=db_finding.affected_entities or [],
            confidence=db_finding.confidence,
            repairability=db_finding.repairability,
        )

        clone_root = Path(snapshot.clone_path)
        wt_manager = WorktreeManager(clone_root)
        wt_path = worktree_path or wt_manager.create_worktree(job.head_sha)
        owns_worktree = worktree_path is None

        sandbox = DockerBackend()
        try:
            baseline = await BaselineValidator.capture_baseline(sandbox, wt_path, job.head_sha)
            commands = BaselineValidator.discover_validation_commands(wt_path)
            plan = ValidationPlan(
                patch_syntax_check=True,
                lint_command=commands.get("lint"),
                type_check_command=commands.get("type_check"),
                targeted_tests_command=commands.get("targeted_test"),
                full_tests_command=commands.get("full_test"),
            )
            return await RepairService.execute_finding_repair(
                session=session,
                job=job,
                snapshot=snapshot,
                graph_version=graph_version,
                pydantic_finding=pydantic_finding,
                worktree_path=wt_path,
                baseline=baseline,
                validation_plan=plan,
            )
        finally:
            if owns_worktree:
                wt_manager.remove_worktree(wt_path)
