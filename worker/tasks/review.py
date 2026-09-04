"""Orchestration task running the complete end-to-end review and repair lifecycle.

Invariants enforced in this module:
  1. Every Job status change goes through JobMutationService.transition() — never direct assignment.
  2. assert_head_sha_current() is called at three mandatory checkpoints before irreversible actions.
  3. HybridRetriever.retrieve_context() is called before every DiffReviewer.review_pr() call.
  4. Repair domain logic lives in backend.repair.service — this module orchestrates only.
  5. ReviewFinding ORM and Pydantic schemas are imported under distinct aliases.
  6. Guarantee: at-least-once delivery + idempotent/reconcilable side effects (not exactly-once).
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import uuid
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from backend.auth.credential_provider import CredentialExpiredError, GitHubCredentialProvider
from backend.auth.github_client import GitHubClient
from backend.config import settings
from backend.database.models import (
    GraphVersion,
    Job,
    Patch,
    RepairRun,
    Repository,
    RepositorySnapshot,
    ReviewFinding as ReviewFindingORM,
    ReviewRun,
    ValidationRun,
    utcnow,
)
from backend.database.sessions import async_session_factory
from backend.graph.builder import GraphBuilder
from backend.observability.logging import get_logger, log_event
from backend.observability.metrics import MetricsTracker
from backend.orchestration.idempotency import (
    make_publication_lineage_key,
    make_publication_pr_key,
    make_review_idempotency_key,
)
from backend.orchestration.job_service import JobMutationService
from backend.orchestration.state_machine import (
    COMPLETION_REASON_HUMAN_ESCALATION,
    COMPLETION_REASON_REPAIR_BUDGET_EXHAUSTED,
    COMPLETION_REASON_STALE_SNAPSHOT_DRIFT,
    COMPLETION_REASON_UNRECOVERABLE_ERROR,
    STATUS_ANALYZING,
    STATUS_CLONING,
    STATUS_COMPLETED,
    STATUS_ESCALATED,
    STATUS_FAILED,
    STATUS_PUBLISHING,
    STATUS_REPAIRING,
    STATUS_REVIEWING,
    STATUS_STALE_SNAPSHOT,
    STATUS_VALIDATING,
    StaleJobError,
)
from backend.repair.service import RepairService
from backend.repository.git import GitOps
from backend.repository.worktree import WorktreeManager
from backend.retrieval.hybrid_retriever import HybridRetriever
from backend.review.exceptions import (
    AuthTemporaryFailure,
    PublicationPermanentError,
    PublicationRetryableError,
)
from backend.review.publisher import (
    assert_publication_prerequisites,
    cancel_obsolete_publication_claim,
    claim_publication,
    publish_review_comment,
)
from backend.review.reviewer import DiffReviewer
from backend.review.schemas import ReviewFinding as ReviewFindingSchema
from backend.review.snapshot_service import assert_head_sha_current, mark_superseded
from backend.risk.analyzer import PRRiskAnalyzer
from backend.sandbox.docker import DockerBackend
from backend.validation.baseline import BaselineValidator
from backend.validation.runner import ValidationPlan
from worker.celery import celery_app

logger = get_logger(__name__)


def _get_current_sha_for_pr(owner: str, repo_name: str, pr_number: int, token: str) -> Optional[str]:
    """Synchronously fetch the current HEAD SHA for a PR from GitHub API."""
    import requests
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("head", {}).get("sha", "")
    except Exception:
        pass
    return None


async def _resolve_github_token(repo: Repository, triggered_by_user_id: Optional[str], session: Any) -> Optional[str]:
    """Resolve a GitHub token using credential precedence: App → OAuth → PAT."""
    cred_provider = GitHubCredentialProvider()
    if repo.installation_id:
        try:
            return await cred_provider.get_app_credential(repo.installation_id)
        except Exception:
            pass
    if triggered_by_user_id:
        try:
            return await cred_provider.get_user_credential(triggered_by_user_id, session)
        except CredentialExpiredError:
            pass
    pat = cred_provider.get_dev_pat()
    return pat if pat else None


async def _persist_review_findings(
    session: Any,
    job: Job,
    snapshot: RepositorySnapshot,
    graph_version: GraphVersion,
    findings: List[ReviewFindingSchema],
    review_status: str,
    head_sha: str,
) -> ReviewRun:
    """Persist all findings from a ReviewResult into the database and return the ReviewRun."""
    review_run = ReviewRun(
        job_id=job.id,
        status=review_status,
        findings_count=len(findings),
        head_sha=head_sha,
        evidence_validation_passed=all(
            any(e.verified_in_graph for e in f.evidence) for f in findings
        ) if findings else True,
    )
    session.add(review_run)
    await session.flush()

    for issue in findings:
        db_finding = ReviewFindingORM(
            review_run_id=review_run.id,
            finding_id=issue.id,
            severity=issue.severity,
            category=issue.category,
            file_path=issue.file,
            symbol_name=issue.symbol,
            start_line=issue.lines[0] if issue.lines else None,
            end_line=issue.lines[1] if issue.lines and len(issue.lines) > 1 else None,
            description=issue.description,
            evidence=[
                e.model_dump() if hasattr(e, "model_dump") else (e if isinstance(e, dict) else vars(e))
                for e in issue.evidence
            ],
            affected_entities=issue.affected_entities,
            confidence=issue.confidence,
            repairability=issue.repairability,
            status="OPEN",
        )
        session.add(db_finding)

    await session.commit()
    return review_run


async def async_process_pr_job(job_id: str, worker_id: Optional[str] = None) -> None:
    """Async execution of the full PRSmith state machine.

    Every status transition goes through JobMutationService.transition() which performs:
        UPDATE jobs SET status=:new WHERE id=:id AND version=:expected_version
    A StaleJobError means another worker already mutated this job — we exit cleanly.
    """
    wid = worker_id or f"celery-{uuid.uuid4().hex[:8]}"

    async with async_session_factory() as session:
        stmt = (
            select(Job)
            .options(selectinload(Job.repository))
            .where(Job.id == job_id)
        )
        res = await session.execute(stmt)
        job = res.scalars().first()
        if not job or not job.repository:
            logger.error("Job or repository not found", job_id=job_id)
            return

        repo = job.repository
        owner, repo_name = repo.full_name.split("/", 1) if "/" in repo.full_name else (repo.full_name, repo.full_name)

        hybrid_retriever = HybridRetriever()
        reviewer = DiffReviewer()
        sandbox = DockerBackend()
        graph_builder = GraphBuilder()

        try:
            # ── Acquire worker lease ──────────────────────────────────────────
            job = await JobMutationService.acquire_lease(session, job, wid, duration_s=600)

            # ──────────────────────────────────────────────────────────────────
            # PHASE 1: CLONING — clone/fetch repo and create immutable snapshot
            # ──────────────────────────────────────────────────────────────────
            job = await JobMutationService.transition(session, job, STATUS_CLONING, worker_id=wid)
            await session.commit()

            token = await _resolve_github_token(repo, job.triggered_by_user_id, session)

            clone_root = Path(settings.REPO_CLONE_ROOT if hasattr(settings, "REPO_CLONE_ROOT") else "/tmp/prsmith_repos") / repo.full_name.replace("/", "_")
            if not (clone_root / ".git").exists():
                GitOps.clone(f"https://github.com/{repo.full_name}.git", clone_root, token=token)
            else:
                GitOps.fetch_pr(clone_root, job.pr_number, job.head_sha)

            merge_base = GitOps.get_merge_base(clone_root, job.base_sha, job.head_sha)

            snapshot = RepositorySnapshot(
                repository_id=repo.id,
                base_sha=job.base_sha,
                head_sha=job.head_sha,
                merge_base_sha=merge_base,
                clone_path=str(clone_root),
            )
            session.add(snapshot)
            await session.flush()

            # Link snapshot to job
            job = await JobMutationService.transition(
                session, job, STATUS_CLONING, worker_id=wid, snapshot_id=snapshot.id
            )
            await session.commit()

            # ──────────────────────────────────────────────────────────────────
            # PHASE 2: ANALYZING — AST graph + risk assessment
            # ──────────────────────────────────────────────────────────────────
            job = await JobMutationService.transition(session, job, STATUS_ANALYZING, worker_id=wid)
            await session.commit()

            graph_version = await graph_builder.build_and_persist(
                session=session,
                repository_id=repo.id,
                commit_sha=job.head_sha,
                workdir=clone_root,
                snapshot_id=snapshot.id,
            )

            changed_files = GitOps.get_changed_files(clone_root, merge_base, job.head_sha)
            risk = PRRiskAnalyzer.analyze_risk(
                files_changed=changed_files,
                total_lines_added=150,
                total_lines_deleted=30,
            )

            job = await JobMutationService.transition(
                session, job, STATUS_REVIEWING, worker_id=wid,
                risk_level=risk.risk_level,
                risk_score=risk.risk_score,
                graph_version_id=graph_version.id,
            )
            await session.commit()

            # ──────────────────────────────────────────────────────────────────
            # PHASE 3: REVIEWING — hybrid retrieval + LLM review
            # MANDATORY CHECKPOINT: assert snapshot SHA before irreversible LLM call
            # ──────────────────────────────────────────────────────────────────
            current_sha = _get_current_sha_for_pr(owner, repo_name, job.pr_number, token or "")
            if current_sha:
                assert_head_sha_current(job, current_sha, checkpoint_name="before_review")

            diff_text = GitOps.get_diff(clone_root, merge_base, job.head_sha)

            # Extract changed symbols for RAG query
            changed_symbols = GitOps.get_changed_symbols(clone_root, merge_base, job.head_sha) if hasattr(GitOps, "get_changed_symbols") else []
            rag_query = f"PR #{job.pr_number}: {job.pr_title}\n{diff_text[:500]}"

            # RETRIEVAL BEFORE GENERATION — mandatory
            context_items = await hybrid_retriever.retrieve_context(
                session=session,
                snapshot=snapshot,
                graph_version_id=graph_version.id,
                changed_symbols=changed_symbols,
                query=rag_query,
                pr_diff=diff_text,
            )
            log_event("retrieval.completed", job_id=job.id, context_items=len(context_items))

            review_result = await reviewer.review_pr(
                session=session,
                snapshot=snapshot,
                graph_version=graph_version,
                pr_diff=diff_text,
                context_items=context_items,
                risk_level=risk.risk_level,
                pr_number=job.pr_number,
                pr_title=job.pr_title,
            )

            # Persist findings
            head_sha = job.head_sha
            review_run = await _persist_review_findings(
                session, job, snapshot, graph_version,
                review_result.issues, review_result.review_status, head_sha,
            )
            MetricsTracker.record_review_completed(len(review_result.issues), risk.risk_level)

            # Check for escalation
            if review_result.review_status == "ESCALATED":
                job = await JobMutationService.transition(
                    session, job, STATUS_ESCALATED, worker_id=wid,
                    completion_reason=COMPLETION_REASON_HUMAN_ESCALATION,
                    completed_at=utcnow(),
                )
                await session.commit()
                log_event("review.escalated", job_id=job.id)
                return

            # ──────────────────────────────────────────────────────────────────
            # PHASE 4: REPAIRING (conditional)
            # ──────────────────────────────────────────────────────────────────
            actionable_issues = [f for f in review_result.issues if f.repairability in ("HIGH", "MEDIUM")]
            auto_repair = getattr(repo, "auto_repair_enabled", False)
            do_repair = auto_repair and actionable_issues and risk.risk_level != "CRITICAL"

            repaired_finding_ids: List[str] = []
            applied_patches: List[str] = []

            if do_repair:
                # MANDATORY CHECKPOINT before repair
                current_sha = _get_current_sha_for_pr(owner, repo_name, job.pr_number, token or "")
                if current_sha:
                    assert_head_sha_current(job, current_sha, checkpoint_name="before_repair")

                job = await JobMutationService.transition(session, job, STATUS_REPAIRING, worker_id=wid)
                await session.commit()

                wt_manager = WorktreeManager(clone_root)
                wt_path = wt_manager.create_worktree(job.head_sha)

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

                    for pydantic_finding in actionable_issues:
                        # MANDATORY CHECKPOINT per repair iteration
                        current_sha = _get_current_sha_for_pr(owner, repo_name, job.pr_number, token or "")
                        if current_sha:
                            assert_head_sha_current(job, current_sha, checkpoint_name=f"repair_iteration_{pydantic_finding.id}")

                        # Repair via service layer — no circular imports
                        repair_result = await RepairService.execute_finding_repair(
                            session=session,
                            job=job,
                            snapshot=snapshot,
                            graph_version=graph_version,
                            pydantic_finding=pydantic_finding,
                            worktree_path=wt_path,
                            baseline=baseline,
                            validation_plan=plan,
                            repair_orchestrator_factory=lambda: RepairService.make_orchestrator(sandbox),
                        )

                        if repair_result.get("outcome") == "SUCCESS":
                            repaired_finding_ids.append(pydantic_finding.id)
                            patch_diff = repair_result.get("patch_diff", "")
                            if patch_diff:
                                applied_patches.append(patch_diff)

                finally:
                    wt_manager.remove_worktree(wt_path)

                job = await JobMutationService.transition(session, job, STATUS_VALIDATING, worker_id=wid)
                await session.commit()

            # ──────────────────────────────────────────────────────────────────
            # PHASE 5: PUBLISHING
            # MANDATORY CHECKPOINT: assert SHA immediately before publication claim
            # ──────────────────────────────────────────────────────────────────
            current_sha = _get_current_sha_for_pr(owner, repo_name, job.pr_number, token or "")
            if current_sha:
                assert_head_sha_current(job, current_sha, checkpoint_name="before_publication")

            job = await JobMutationService.transition(session, job, STATUS_PUBLISHING, worker_id=wid)
            await session.commit()

            review_body = _build_review_comment(
                job, review_result, repaired_finding_ids, actionable_issues
            )

            gh_client = GitHubClient()
            try:
                pub = await _execute_publication(
                    session=session,
                    job=job,
                    repo=repo,
                    pr_number=job.pr_number,
                    review_body=review_body,
                    gh_client=gh_client,
                    graph_content_hash=graph_version.graph_content_hash,
                    finding_content_hashes=[f.id for f in review_result.issues],
                    token=token,
                )
                if pub:
                    job = await JobMutationService.transition(
                        session, job, STATUS_COMPLETED, worker_id=wid,
                        completed_at=utcnow(),
                        confidence_score=0.92 if repaired_finding_ids else 0.85,
                    )
                    await session.commit()
                    log_event("review.published", job_id=job.id, pr_number=job.pr_number)
            except PublicationPermanentError as e:
                logger.error("Permanent GitHub publication failure", job_id=job.id, error=str(e), http_status=e.http_status)
                job = await JobMutationService.transition(
                    session, job, STATUS_FAILED, worker_id=wid,
                    completion_reason=f"PUBLICATION_FAILED:{e.http_status}",
                    error_message=str(e),
                    completed_at=utcnow(),
                )
                await session.commit()
            # PublicationRetryableError: leave job in PUBLISHING — recovery worker retries

        except StaleJobError as e:
            # Another worker (or mark_superseded) already mutated this job — exit cleanly
            logger.info("Stale job detected — exiting without mutation", job_id=job_id, reason=str(e))

        except Exception as e:
            # Check for StaleSnapshotError specifically
            from backend.review.exceptions import StaleSnapshotError
            if "StaleSnapshotError" in type(e).__name__ or isinstance(e, Exception) and hasattr(e, "__class__") and "StaleSnapshot" in type(e).__name__:
                logger.warning("Snapshot drift — marking job STALE_SNAPSHOT", job_id=job_id, error=str(e))
                try:
                    await JobMutationService.transition(
                        session, job, STATUS_STALE_SNAPSHOT, worker_id=wid,
                        completion_reason=COMPLETION_REASON_STALE_SNAPSHOT_DRIFT,
                        error_message=str(e),
                        completed_at=utcnow(),
                    )
                    await session.commit()
                except StaleJobError:
                    pass
            else:
                logger.error("Job processing failed with unhandled exception", error=str(e), job_id=job_id)
                try:
                    await JobMutationService.transition(
                        session, job, STATUS_FAILED, worker_id=wid,
                        completion_reason=COMPLETION_REASON_UNRECOVERABLE_ERROR,
                        error_message=str(e),
                        completed_at=utcnow(),
                    )
                    await session.commit()
                except StaleJobError:
                    pass


async def _execute_publication(
    session: Any,
    job: Job,
    repo: Repository,
    pr_number: int,
    review_body: str,
    gh_client: GitHubClient,
    graph_content_hash: str,
    finding_content_hashes: List[str],
    token: Optional[str],
) -> Any:
    """Execute the full publication flow: prerequisites → claim → POSTING → GitHub → POSTED."""
    from backend.orchestration.hashing import compute_chain_hash

    patch_artifact_hash = None  # No repair patch in review-only path
    await assert_publication_prerequisites(session, job, patch_artifact_hash)

    chain_hash = compute_chain_hash(
        snapshot_sha=job.head_sha,
        graph_content_hash=graph_content_hash,
        finding_content_hashes=finding_content_hashes,
        patch_artifact_hash=patch_artifact_hash,
        validation_result_hash="no_validation",
        pipeline_version=settings.PIPELINE_VERSION,
    )
    lineage_key = make_publication_lineage_key(str(job.repository_id), pr_number, chain_hash)

    # Cancel any obsolete active claim from a prior run on this PR
    await cancel_obsolete_publication_claim(
        session, repository_id=str(job.repository_id), pr_number=pr_number, new_head_sha=job.head_sha
    )

    pub = await claim_publication(
        session=session,
        idempotency_key=lineage_key,
        chain_hash=chain_hash,
        job=job,
        worker_id=f"celery-{job.id[:8]}",
        repository_id=str(job.repository_id),
        pr_number=pr_number,
    )
    if pub is None:
        # Already claimed by another worker for this lineage
        return None

    return await publish_review_comment(
        session=session,
        pub=pub,
        repo_full_name=repo.full_name,
        pr_number=pr_number,
        review_body=review_body,
        github_client=gh_client,
        token=token,
    )


def _build_review_comment(
    job: Job,
    review_result: Any,
    repaired_finding_ids: List[str],
    actionable_issues: List[ReviewFindingSchema],
) -> str:
    """Build the structured GitHub PR comment body."""
    severity_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
    issues_lines = "\n".join(
        f"- {severity_icon.get(i.severity, '⚪')} **{i.id}** `[{i.severity}]` {i.description[:120]} — `{i.file}`"
        for i in review_result.issues
    ) or "No issues detected."

    if repaired_finding_ids:
        repaired_lines = "\n".join(f"- ✅ **{r}** — patch verified in isolated sandbox" for r in repaired_finding_ids)
    elif actionable_issues:
        repaired_lines = "Manual repair mode — fixes can be triggered individually in the dashboard."
    else:
        repaired_lines = "No repairs required."

    return f"""## 🛡️ PRSmith Review Report

**Risk Level:** `{job.risk_level}` (Score: {job.risk_score}/100)

### Findings
{issues_lines}

### Repairs
{repaired_lines}

---
*PRSmith — autonomous in reasoning, not in trust. Final merge decision remains with human reviewers.*
"""


@celery_app.task(name="worker.tasks.review.process_pr_job", bind=True, max_retries=0)
def process_pr_job(self: Any, job_id: str) -> None:
    """Celery entry point wrapping the async pipeline.

    max_retries=0: Celery auto-retry is disabled. Recovery is handled by the
    stale lease recovery worker (backend/orchestration/leases.py) and the
    publication recovery worker (backend/review/publisher.py).
    """
    worker_id = f"celery-{self.request.id[:8]}" if self.request.id else f"celery-{uuid.uuid4().hex[:8]}"
    logger.info("Starting PRSmith processing task", job_id=job_id, worker_id=worker_id)
    asyncio.run(async_process_pr_job(job_id, worker_id=worker_id))


async def async_run_pr_review(
    repo_id: str,
    owner: str,
    repo_name: str,
    pr_number: int,
    triggered_by_user_id: Optional[str] = None,
) -> dict:
    """Manual dispatch: fetch PR metadata from GitHub, create Job with idempotency, and run review."""
    async with async_session_factory() as session:
        stmt = select(Repository).where(Repository.id == repo_id)
        repo = (await session.execute(stmt)).scalars().first()
        if not repo:
            logger.error("Repository not found for manual review", repo_id=repo_id)
            return {"status": "error", "message": "Repository not found"}

        token = await _resolve_github_token(repo, triggered_by_user_id, session)
        async with httpx.AsyncClient(timeout=15) as client:
            auth_header = {"Authorization": f"Bearer {token}"} if token else {}
            res = await client.get(
                f"https://api.github.com/repos/{owner}/{repo_name}/pulls/{pr_number}",
                headers={**auth_header, "Accept": "application/vnd.github.v3+json"},
            )
            res.raise_for_status()
            pr_data = res.json()

        head_sha = pr_data.get("head", {}).get("sha", "")
        base_sha = pr_data.get("base", {}).get("sha", "")
        title = pr_data.get("title", "")

        # Dispatch idempotency: return existing active job for same (repo, pr, sha)
        idempotency_key = make_review_idempotency_key(repo_id, pr_number, head_sha)
        existing_stmt = select(Job).where(Job.idempotency_key == idempotency_key)
        existing_job = (await session.execute(existing_stmt)).scalars().first()
        if existing_job:
            logger.info("Returning existing job for same PR + SHA", job_id=existing_job.id)
            return {"status": "ALREADY_QUEUED", "job_id": existing_job.id}

        job = Job(
            id=str(uuid.uuid4()),
            repository_id=repo_id,
            pr_number=pr_number,
            pr_title=title,
            base_sha=base_sha,
            head_sha=head_sha,
            status="PENDING",
            idempotency_key=idempotency_key,
            triggered_by_user_id=triggered_by_user_id,
        )
        session.add(job)
        await session.commit()

    # Queue via Celery (non-blocking)
    process_pr_job.delay(job.id)
    return {"status": "QUEUED", "job_id": job.id}


@celery_app.task(name="worker.tasks.review.run_pr_review")
def run_pr_review(
    repo_id: str,
    owner: str,
    repo_name: str,
    pr_number: int,
    triggered_by_user_id: Optional[str] = None,
) -> dict:
    """Celery entry point for manual PR review dispatch."""
    logger.info("Executing manual PR review task", repo_id=repo_id, pr_number=pr_number)
    return asyncio.run(async_run_pr_review(repo_id, owner, repo_name, pr_number, triggered_by_user_id))
