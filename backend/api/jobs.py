"""REST API endpoints for inspecting Job states, reviews, repairs, and validations."""

from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.auth.security import get_current_user
from backend.database.models import (
    Job,
    Patch,
    RepairRun,
    ReviewFinding,
    ReviewRun,
    User,
    UserRepository,
    ValidationRun,
)
from backend.database.sessions import get_db_session

router = APIRouter(prefix="/jobs", tags=["Jobs"])


async def _verify_job_access_or_403(job_id: str, user: User, session: AsyncSession) -> Job:
    """Verify tenant authorization for inspecting or mutating job state."""
    stmt = (
        select(Job)
        .options(
            selectinload(Job.repository),
            selectinload(Job.review_runs).selectinload(ReviewRun.findings),
            selectinload(Job.repair_runs).selectinload(RepairRun.iterations),
            selectinload(Job.validation_runs).selectinload(ValidationRun.results),
        )
        .where(Job.id == job_id)
    )
    res = await session.execute(stmt)
    job = res.scalars().first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    ur_stmt = select(UserRepository).where(
        UserRepository.repository_id == job.repository_id,
        UserRepository.user_id == user.id,
    )
    ur = (await session.execute(ur_stmt)).scalars().first()
    if not ur:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: You do not have access to this job")
    return job


@router.get("", response_model=List[Dict[str, Any]])
async def list_jobs(
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """List recent PR analysis and repair jobs for repositories tracked by the authenticated user."""
    stmt = (
        select(Job)
        .join(UserRepository, UserRepository.repository_id == Job.repository_id)
        .where(UserRepository.user_id == current_user.id)
        .options(selectinload(Job.repository))
        .order_by(desc(Job.created_at))
        .limit(limit)
        .offset(offset)
    )
    res = await session.execute(stmt)
    jobs = res.scalars().all()

    return [
        {
            "id": j.id,
            "repository": j.repository.full_name if j.repository else "unknown",
            "pr_number": j.pr_number,
            "pr_title": j.pr_title,
            "status": j.status,
            "risk_level": j.risk_level,
            "confidence_score": j.confidence_score,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        }
        for j in jobs
    ]


@router.get("/{job_id}")
async def get_job_detail(
    job_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Get full state and lifecycle details for a specific job."""
    job = await _verify_job_access_or_403(job_id, current_user, session)

    return {
        "id": job.id,
        "repository": job.repository.full_name if job.repository else "unknown",
        "pr_number": job.pr_number,
        "pr_title": job.pr_title,
        "base_sha": job.base_sha,
        "head_sha": job.head_sha,
        "status": job.status,
        "risk_level": job.risk_level,
        "risk_score": job.risk_score,
        "confidence_score": job.confidence_score,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "review_runs": [
            {
                "id": r.id,
                "status": r.status,
                "findings": [
                    {
                        "id": f.id,
                        "finding_id": f.finding_id,
                        "severity": f.severity,
                        "category": f.category,
                        "file_path": f.file_path,
                        "symbol_name": f.symbol_name,
                        "description": f.description,
                        "confidence": f.confidence,
                        "repairability": f.repairability,
                        "status": f.status,
                        "evidence": f.evidence,
                        "affected_entities": f.affected_entities,
                    }
                    for f in r.findings
                ],
            }
            for r in job.review_runs
        ],
        "repair_runs": [
            {
                "id": rep.id,
                "status": rep.status,
                "iterations": [
                    {
                        "iteration_number": it.iteration_number,
                        "status": it.status,
                        "validation_status": it.validation_status,
                        "error_summary": it.error_summary,
                    }
                    for it in rep.iterations
                ],
            }
            for rep in job.repair_runs
        ],
        "validation_runs": [
            {
                "id": v.id,
                "stage": v.stage,
                "passed": v.passed,
                "duration_ms": v.duration_ms,
                "results": [
                    {
                        "layer": vr.layer_name,
                        "command": vr.command,
                        "passed": vr.passed,
                        "exit_code": vr.exit_code,
                        "stdout": vr.stdout,
                        "stderr": vr.stderr,
                        "failure_class": vr.failure_class,
                    }
                    for vr in v.results
                ],
            }
            for v in job.validation_runs
        ],
    }


@router.get("/{job_id}/patches")
async def get_job_patches(
    job_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> List[Dict[str, Any]]:
    """Get all generated patches for an authorized job."""
    await _verify_job_access_or_403(job_id, current_user, session)
    stmt = select(Patch).where(Patch.job_id == job_id).order_by(Patch.created_at)
    res = await session.execute(stmt)
    patches = res.scalars().all()

    return [
        {
            "id": p.id,
            "diff_hash": p.diff_hash,
            "diff_content": p.diff_content,
            "files_changed": p.files_changed,
            "lines_added": p.lines_added,
            "lines_deleted": p.lines_deleted,
            "is_valid_syntax": p.is_valid_syntax,
            "is_within_scope": p.is_within_scope,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in patches
    ]


@router.post("/{job_id}/findings/{finding_id}/repair")
async def repair_single_finding(
    job_id: str,
    finding_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Trigger manual targeted patch repair for a specific review finding on an authorized job."""
    job = await _verify_job_access_or_403(job_id, current_user, session)

    finding_stmt = select(ReviewFinding).where(ReviewFinding.id == finding_id)
    finding = (await session.execute(finding_stmt)).scalars().first()
    if not finding:
        raise HTTPException(status_code=404, detail="Review finding not found")

    try:
        from worker.tasks.review import run_finding_repair  # type: ignore[import]
        task = run_finding_repair.apply_async(
            kwargs={"job_id": job_id, "finding_id": finding_id},
            queue="repair",
        )
        return {
            "status": "QUEUED",
            "task_id": task.id,
            "job_id": job_id,
            "finding_id": finding_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to queue repair task: {exc}") from exc
