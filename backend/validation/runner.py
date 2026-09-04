"""Validation plan runner executing layered deterministic checks."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from backend.observability.logging import get_logger, log_event
from backend.observability.metrics import VALIDATION_DURATION_SECONDS
from backend.repository.git import GitOps
from backend.sandbox.interface import SandboxBackend, SandboxResult
from backend.validation.classifiers import FailureClassifier, FailureClass

logger = get_logger(__name__)


@dataclass
class ValidationPlan:
    """Ordered validation commands for a repository."""

    patch_syntax_check: bool = True
    lint_command: Optional[List[str]] = None
    type_check_command: Optional[List[str]] = None
    targeted_tests_command: Optional[List[str]] = None
    integration_tests_command: Optional[List[str]] = None
    full_tests_command: Optional[List[str]] = None
    build_command: Optional[List[str]] = None


class ValidationRunner:
    """Executes the validation layers in increasing order of cost."""

    def __init__(self, sandbox: SandboxBackend):
        self.sandbox = sandbox

    async def execute_plan(
        self,
        worktree_path: Path,
        plan: ValidationPlan,
        patch_diff: Optional[str] = None,
    ) -> Dict[str, SandboxResult]:
        """Execute validation layers and record outputs."""
        results: Dict[str, SandboxResult] = {}

        # Layer 0: Patch Syntax Check
        if plan.patch_syntax_check and patch_diff:
            ok, err = GitOps.check_patch(worktree_path, patch_diff)
            results["patch_syntax"] = SandboxResult(
                command="git apply --check",
                exit_code=0 if ok else 1,
                stderr=err,
            )
            if not ok:
                logger.warning("Patch syntax validation failed", error=err)
                return results

        # Layer 1: Formatting and Linting
        if plan.lint_command:
            res = await self.sandbox.run_command(plan.lint_command, worktree_path)
            results["lint"] = res
            if not res.passed:
                logger.info("Lint check failed", output=res.stdout + res.stderr)

        # Layer 2: Type Checking
        if plan.type_check_command:
            res = await self.sandbox.run_command(plan.type_check_command, worktree_path)
            results["type_check"] = res
            if not res.passed:
                logger.info("Type check failed", output=res.stdout + res.stderr)

        # Layer 3: Targeted Tests
        if plan.targeted_tests_command:
            res = await self.sandbox.run_command(plan.targeted_tests_command, worktree_path)
            results["targeted_test"] = res

        # Layer 4: Integration / Broader Tests
        if plan.integration_tests_command and results.get("targeted_test", SandboxResult(command="", exit_code=0)).passed:
            res = await self.sandbox.run_command(plan.integration_tests_command, worktree_path)
            results["integration_test"] = res

        # Layer 5: Build Check
        if plan.build_command:
            res = await self.sandbox.run_command(plan.build_command, worktree_path)
            results["build"] = res

        return results
