"""Baseline validation discovery and execution."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from backend.observability.logging import get_logger
from backend.sandbox.interface import SandboxBackend, SandboxResult

logger = get_logger(__name__)


@dataclass
class BaselineResult:
    """Pre-repair repository validation snapshot."""

    commit_sha: str
    lint_passed: bool
    type_passed: bool
    test_passed: bool
    build_passed: bool
    failed_tests: List[str] = field(default_factory=list)
    raw_results: Dict[str, SandboxResult] = field(default_factory=dict)
    captured_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_healthy(self) -> bool:
        return self.lint_passed and self.type_passed and self.test_passed


class BaselineValidator:
    """Discovers project commands and executes baseline checks before AI repair."""

    @staticmethod
    def discover_validation_commands(repo_path: Path) -> Dict[str, List[str]]:
        """Inspect repository files to configure validation commands."""
        commands = {}

        pyproject = repo_path / "pyproject.toml"
        package_json = repo_path / "package.json"
        makefile = repo_path / "Makefile"

        # Python discovery
        if pyproject.exists() or any(repo_path.glob("*.py")):
            commands["lint"] = ["ruff", "check", "."]
            commands["type_check"] = ["mypy", "."]
            commands["targeted_test"] = ["pytest", "-v", "--tb=short"]
            commands["full_test"] = ["pytest", "-v"]

        # JS/TS discovery
        elif package_json.exists():
            commands["lint"] = ["npm", "run", "lint"]
            commands["targeted_test"] = ["npm", "test"]
            commands["build"] = ["npm", "run", "build"]

        return commands

    @classmethod
    async def capture_baseline(
        cls,
        sandbox: SandboxBackend,
        worktree_path: Path,
        commit_sha: str,
    ) -> BaselineResult:
        """Run available checks on unmodified PR HEAD to establish baseline."""
        logger.info("Capturing validation baseline on unmodified worktree", commit_sha=commit_sha)
        commands = cls.discover_validation_commands(worktree_path)

        raw_results = {}
        lint_passed = True
        type_passed = True
        test_passed = True
        build_passed = True
        failed_tests = []

        if "lint" in commands:
            res = await sandbox.run_command(commands["lint"], worktree_path)
            raw_results["lint"] = res
            lint_passed = res.passed

        if "type_check" in commands:
            res = await sandbox.run_command(commands["type_check"], worktree_path)
            raw_results["type_check"] = res
            type_passed = res.passed

        if "targeted_test" in commands:
            res = await sandbox.run_command(commands["targeted_test"], worktree_path)
            raw_results["targeted_test"] = res
            test_passed = res.passed
            if not res.passed:
                # Simple extraction of failed test names from pytest output
                for line in (res.stdout + res.stderr).splitlines():
                    if "FAILED " in line:
                        failed_tests.append(line.replace("FAILED ", "").strip())

        return BaselineResult(
            commit_sha=commit_sha,
            lint_passed=lint_passed,
            type_passed=type_passed,
            test_passed=test_passed,
            build_passed=build_passed,
            failed_tests=failed_tests,
            raw_results=raw_results,
        )
