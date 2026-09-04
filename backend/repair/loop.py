"""Iterative repair loop orchestrator with dynamic RAG and stall detection."""

import hashlib
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database.models import GraphVersion, RepositorySnapshot
from backend.observability.logging import get_logger, log_event
from backend.repair.patch_policy import PatchPolicyEnforcer
from backend.repair.patcher import WorktreePatcher
from backend.repair.repairer import RepairAgent
from backend.retrieval.hybrid_retriever import HybridRetriever
from backend.review.schemas import ReviewFinding
from backend.sandbox.interface import SandboxBackend
from backend.validation.baseline import BaselineResult
from backend.validation.classifiers import FailureClassifier, FailureClass
from backend.validation.differential import DifferentialValidator
from backend.validation.runner import ValidationPlan, ValidationRunner

logger = get_logger(__name__)


class RepairLoopOrchestrator:
    """Manages the iterative repair feedback loop for actionable findings."""

    def __init__(
        self,
        sandbox: SandboxBackend,
        hybrid_retriever: Optional[HybridRetriever] = None,
        repairer: Optional[RepairAgent] = None,
    ):
        self.sandbox = sandbox
        self.retriever = hybrid_retriever or HybridRetriever()
        self.repairer = repairer or RepairAgent()
        self.validation_runner = ValidationRunner(sandbox)
        self.policy_enforcer = PatchPolicyEnforcer()
        self.patcher = WorktreePatcher()

    async def execute_repair(
        self,
        session: AsyncSession,
        snapshot: RepositorySnapshot,
        graph_version: GraphVersion,
        worktree_path: Path,
        finding: ReviewFinding,
        baseline: BaselineResult,
        validation_plan: ValidationPlan,
    ) -> Dict[str, Any]:
        """Execute iterative repair loop for a single finding."""
        start_time = time.time()
        max_iterations = settings.MAX_REPAIR_ITERATIONS
        max_time = settings.MAX_REPAIR_TIME_SECONDS

        seen_states = set()
        last_error = None
        applied_successful_diffs = []

        for iteration in range(1, max_iterations + 1):
            elapsed = time.time() - start_time
            if elapsed > max_time:
                log_event("repair.timeout", finding_id=finding.id, elapsed=elapsed)
                return {
                    "outcome": "TIMEOUT",
                    "iterations": iteration,
                    "applied_patches": applied_successful_diffs,
                    "message": f"Repair budget exceeded {max_time} seconds.",
                }

            # 1. Dynamic RAG: Retrieve context (using error feedback if on retry)
            query_text = f"{finding.symbol or finding.file} {finding.description}"
            if last_error:
                query_text += f" {last_error[:200]}"

            context_items = await self.retriever.retrieve_context(
                session=session,
                snapshot=snapshot,
                graph_version_id=graph_version.id,
                changed_symbols=[finding.symbol] if finding.symbol else [],
                query=query_text,
                pr_diff="",
            )

            # 2. Generate patch
            proposal = await self.repairer.generate_repair_patch(
                finding=finding,
                context_items=context_items,
                iteration=iteration,
                previous_error_feedback=last_error,
            )

            if not proposal.unified_diff.strip():
                log_event("repair.retry", reason="empty_patch", iteration=iteration)
                last_error = "Generated patch was empty."
                continue

            # 3. Stall detection (§49): Detect identical patch and error states
            state_hash = hashlib.sha256(
                f"{proposal.unified_diff}:{last_error or ''}".encode()
            ).hexdigest()

            if state_hash in seen_states:
                log_event("job.escalated", reason="repair_stalled_loop", finding_id=finding.id)
                return {
                    "outcome": "REPAIR_STALLED",
                    "iterations": iteration,
                    "applied_patches": applied_successful_diffs,
                    "message": "Repair stalled: Repeated identical patch produced without progress.",
                }
            seen_states.add(state_hash)

            # 4. Patch Scope Policy Check (§29)
            scope_result = self.policy_enforcer.evaluate_patch(proposal.unified_diff)
            if not scope_result.is_allowed:
                last_error = f"Patch rejected by policy: {'; '.join(scope_result.reasons)}"
                continue

            # 5. Apply Patch to Worktree (Layer 0 check included)
            apply_ok, apply_err = self.patcher.apply_patch_checkpoint(
                worktree_path, proposal.unified_diff
            )
            if not apply_ok:
                last_error = apply_err
                continue

            # 6. Run Layered Validation
            log_event("validation.started", iteration=iteration)
            val_results = await self.validation_runner.execute_plan(
                worktree_path, validation_plan
            )

            # 7. Differential Validation
            diff_result = DifferentialValidator.compare(baseline, val_results)

            if diff_result.outcome == "SUCCESS":
                log_event("validation.passed", iteration=iteration, finding_id=finding.id)
                applied_successful_diffs.append(proposal.unified_diff)
                return {
                    "outcome": "SUCCESS",
                    "iterations": iteration,
                    "applied_patches": applied_successful_diffs,
                    "patch_diff": proposal.unified_diff,
                    "explanation": proposal.explanation,
                }

            # If validation failed, classify and decide next action
            failed_layer = "targeted_test" if not val_results.get("targeted_test", BaselineResult(commit_sha="", lint_passed=True, type_passed=True, test_passed=True, build_passed=True)).test_passed else "lint"
            failed_res = val_results.get(failed_layer)
            stdout = failed_res.stdout if failed_res else ""
            stderr = failed_res.stderr if failed_res else ""

            failure_class = FailureClassifier.classify(
                failed_layer, failed_res.exit_code if failed_res else 1, stdout, stderr
            )

            log_event("validation.failed", failure_class=failure_class.value, iteration=iteration)

            # If it's an environment failure (OOM, etc.), don't ask LLM to modify code
            if not FailureClassifier.is_repairable(failure_class):
                self.patcher.rollback_patch(worktree_path)
                return {
                    "outcome": "ENVIRONMENT_FAILURE",
                    "iterations": iteration,
                    "applied_patches": applied_successful_diffs,
                    "message": f"Validation failed with environment error: {failure_class.value}",
                }

            # Rollback failed patch for clean slate in next iteration (§46)
            self.patcher.rollback_patch(worktree_path)
            last_error = f"Validation failed at layer [{failed_layer}]:\n{stdout}\n{stderr}"

        return {
            "outcome": "FAILED",
            "iterations": max_iterations,
            "applied_patches": applied_successful_diffs,
            "message": f"Repair budget of {max_iterations} iterations exhausted.",
        }
