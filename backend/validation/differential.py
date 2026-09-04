"""Differential validation comparison between baseline and repaired states."""

from dataclasses import dataclass, field
from typing import Dict, List, Literal
from backend.sandbox.interface import SandboxResult
from backend.validation.baseline import BaselineResult


@dataclass
class DifferentialResult:
    """Outcome of comparing baseline validation with post-repair validation."""

    outcome: Literal["SUCCESS", "REGRESSION", "NO_CHANGE", "PARTIAL_REPAIR"]
    resolved_failures: List[str] = field(default_factory=list)
    new_regressions: List[str] = field(default_factory=list)
    remaining_failures: List[str] = field(default_factory=list)
    is_lint_clean: bool = True
    is_types_clean: bool = True

    @property
    def is_acceptable(self) -> bool:
        """True if no regressions were introduced and lint/types are clean."""
        return len(self.new_regressions) == 0 and self.is_lint_clean and self.is_types_clean


class DifferentialValidator:
    """Evaluates whether a repair improved code health without introducing regressions."""

    @staticmethod
    def compare(
        baseline: BaselineResult,
        post_repair_results: Dict[str, SandboxResult],
    ) -> DifferentialResult:
        """Perform differential comparison."""
        current_failed_tests = []
        test_res = post_repair_results.get("targeted_test") or post_repair_results.get("full_test")
        if test_res and not test_res.passed:
            for line in (test_res.stdout + test_res.stderr).splitlines():
                if "FAILED " in line:
                    current_failed_tests.append(line.replace("FAILED ", "").strip())

        base_failures_set = set(baseline.failed_tests)
        current_failures_set = set(current_failed_tests)

        resolved = list(base_failures_set - current_failures_set)
        regressions = list(current_failures_set - base_failures_set)
        remaining = list(base_failures_set & current_failures_set)

        lint_res = post_repair_results.get("lint")
        type_res = post_repair_results.get("type_check")

        is_lint_clean = lint_res.passed if lint_res else True
        is_types_clean = type_res.passed if type_res else True

        if regressions:
            outcome = "REGRESSION"
        elif resolved and not current_failed_tests:
            outcome = "SUCCESS"
        elif resolved and remaining:
            outcome = "PARTIAL_REPAIR"
        elif not baseline.failed_tests and not current_failed_tests and is_lint_clean and is_types_clean:
            outcome = "SUCCESS"
        else:
            outcome = "NO_CHANGE"

        return DifferentialResult(
            outcome=outcome,
            resolved_failures=resolved,
            new_regressions=regressions,
            remaining_failures=remaining,
            is_lint_clean=is_lint_clean,
            is_types_clean=is_types_clean,
        )
