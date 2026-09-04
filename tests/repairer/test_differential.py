import pytest
from backend.sandbox.interface import SandboxResult
from backend.validation.baseline import BaselineResult
from backend.validation.differential import DifferentialValidator


def test_differential_success():
    baseline = BaselineResult(
        commit_sha="sha1",
        lint_passed=True,
        type_passed=True,
        test_passed=False,
        build_passed=True,
        failed_tests=["test_service.py::test_charge"],
    )

    post_repair = {
        "targeted_test": SandboxResult(command="pytest", exit_code=0, stdout="1 passed in 0.05s"),
        "lint": SandboxResult(command="ruff", exit_code=0),
        "type_check": SandboxResult(command="mypy", exit_code=0),
    }

    diff_res = DifferentialValidator.compare(baseline, post_repair)
    assert diff_res.outcome == "SUCCESS"
    assert "test_service.py::test_charge" in diff_res.resolved_failures
    assert len(diff_res.new_regressions) == 0


def test_differential_regression():
    baseline = BaselineResult(
        commit_sha="sha1",
        lint_passed=True,
        type_passed=True,
        test_passed=True,
        build_passed=True,
        failed_tests=[],
    )

    post_repair = {
        "targeted_test": SandboxResult(
            command="pytest", exit_code=1, stdout="FAILED test_invoice.py::test_create"
        ),
    }

    diff_res = DifferentialValidator.compare(baseline, post_repair)
    assert diff_res.outcome == "REGRESSION"
    assert "test_invoice.py::test_create" in diff_res.new_regressions
