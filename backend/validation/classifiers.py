"""Failure classification mapping validation output to root failure causes."""

import re
from enum import Enum
from typing import Optional


class FailureClass(str, Enum):
    PATCH_SYNTAX = "PATCH_SYNTAX"
    COMPILE_ERROR = "COMPILE_ERROR"
    TYPE_ERROR = "TYPE_ERROR"
    LINT_ERROR = "LINT_ERROR"
    TEST_FAILURE = "TEST_FAILURE"
    TIMEOUT = "TIMEOUT"
    OOM = "OOM"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    UNKNOWN = "UNKNOWN"


class FailureClassifier:
    """Classifies errors from compiler, linter, type-checker, and test runners."""

    @staticmethod
    def classify(layer: str, exit_code: int, stdout: str, stderr: str) -> FailureClass:
        combined = f"{stdout}\n{stderr}".lower()

        if "out of memory" in combined or "oomkilled" in combined or "killed: 9" in combined:
            return FailureClass.OOM

        if "timed out" in combined or exit_code == -1:
            return FailureClass.TIMEOUT

        if "patch does not apply" in combined or "corrupt patch" in combined or layer == "patch_syntax":
            return FailureClass.PATCH_SYNTAX

        if "modulenotfounderror" in combined or "no module named" in combined or "importerror" in combined:
            return FailureClass.DEPENDENCY_FAILURE

        if "syntaxerror" in combined or "indentationerror" in combined:
            return FailureClass.COMPILE_ERROR

        if "error:" in combined and ("mypy" in layer or "type" in layer or "incompatible type" in combined):
            return FailureClass.TYPE_ERROR

        if "ruff" in layer or "flake8" in layer or "eslint" in layer:
            return FailureClass.LINT_ERROR

        if "failed" in combined or "failures" in combined or "assert" in combined or "pytest" in layer:
            return FailureClass.TEST_FAILURE

        if "permission denied" in combined or "connection refused" in combined or "docker" in combined:
            return FailureClass.ENVIRONMENT_FAILURE

        return FailureClass.UNKNOWN

    @staticmethod
    def is_repairable(failure_class: FailureClass) -> bool:
        """Determines if a failure is code-caused and can be resolved by the Repair Agent."""
        return failure_class in [
            FailureClass.PATCH_SYNTAX,
            FailureClass.COMPILE_ERROR,
            FailureClass.TYPE_ERROR,
            FailureClass.LINT_ERROR,
            FailureClass.TEST_FAILURE,
        ]
