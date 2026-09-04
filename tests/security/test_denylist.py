"""Unit and security tests for categorized denylists, YAML semantic classification, and AST escalation."""

import pytest
from backend.repair.patch_policy import (
    CATEGORY_CI_WORKFLOWS,
    CATEGORY_DEPENDENCIES,
    CATEGORY_INFRASTRUCTURE,
    CATEGORY_SECRETS,
    PatchPolicyEnforcer,
    classify_yaml_path,
)
from backend.review.exceptions import DenylistViolation


def make_diff(file_path: str, added_code: str = "pass\n") -> str:
    """Generate a minimal valid unified diff modifying file_path."""
    lines = added_code.splitlines(True)
    added_diff = "".join(f"+{line}" if not line.startswith("+") else line for line in lines)
    if not added_diff.endswith("\n"):
        added_diff += "\n"
    return f"""--- a/{file_path}
+++ b/{file_path}
@@ -1,3 +1,4 @@
 def example():
{added_diff}
"""


def test_yaml_semantic_classification():
    """classify_yaml_path distinguishes between deployment infra vs application configurations."""
    # Infrastructure YAML
    assert classify_yaml_path(".github/workflows/build.yml") == "INFRASTRUCTURE"
    assert classify_yaml_path("k8s/deployment.yaml") == "INFRASTRUCTURE"
    assert classify_yaml_path("helm/charts/values.yaml") == "INFRASTRUCTURE"
    assert classify_yaml_path("docker-compose.prod.yml") == "INFRASTRUCTURE"
    assert classify_yaml_path("deploy/service.yaml") == "INFRASTRUCTURE"

    # Application YAML
    assert classify_yaml_path("docs/mkdocs.yml") == "APPLICATION"
    assert classify_yaml_path("api/openapi.yaml") == "APPLICATION"
    assert classify_yaml_path("locales/en.yml") == "APPLICATION"
    assert classify_yaml_path("fixtures/sample_users.yaml") == "APPLICATION"


def test_categorized_denylist_rejection():
    """Modifying files in categorized denylist triggers REJECT with specific category."""
    # 1. Secrets
    res_secret = PatchPolicyEnforcer.evaluate_patch(make_diff(".env.production"))
    assert res_secret.action == "REJECT"
    assert CATEGORY_SECRETS in res_secret.category_violations

    # 2. CI Workflows
    res_ci = PatchPolicyEnforcer.evaluate_patch(make_diff(".github/workflows/ci.yml"))
    assert res_ci.action == "REJECT"
    assert CATEGORY_CI_WORKFLOWS in res_ci.category_violations

    # 3. Infrastructure
    res_docker = PatchPolicyEnforcer.evaluate_patch(make_diff("Dockerfile"))
    assert res_docker.action == "REJECT"
    assert CATEGORY_INFRASTRUCTURE in res_docker.category_violations

    # 4. Dependencies
    res_dep = PatchPolicyEnforcer.evaluate_patch(make_diff("package-lock.json"))
    assert res_dep.action == "REJECT"
    assert CATEGORY_DEPENDENCIES in res_dep.category_violations

    # assert_patch_allowed raises DenylistViolation
    with pytest.raises(DenylistViolation):
        PatchPolicyEnforcer.assert_patch_allowed(res_secret)


def test_application_yaml_is_permitted():
    """Application YAML files (OpenAPI, docs) are allowed and not rejected."""
    res_app_yaml = PatchPolicyEnforcer.evaluate_patch(make_diff("openapi.yaml"))
    assert res_app_yaml.action == "ALLOW"
    assert res_app_yaml.is_allowed is True
    assert len(res_app_yaml.reasons) == 0


def test_ast_escalation_triggers_escalate_never_reject():
    """Suspicious Python AST patterns trigger ESCALATE (human review), not REJECT."""
    # 1. eval() call
    diff_eval = make_diff("src/calculator.py", "result = eval(user_expr)\n")
    res_eval = PatchPolicyEnforcer.evaluate_patch(diff_eval)
    assert res_eval.action == "ESCALATE"
    assert res_eval.is_allowed is False
    assert any("eval" in reason for reason in res_eval.escalation_reasons)
    # Crucially, reasons (hard rejections) must be empty
    assert len(res_eval.reasons) == 0

    # 2. os.system() call
    diff_system = make_diff("src/utils.py", "os.system('cleanup.sh')\n")
    res_system = PatchPolicyEnforcer.evaluate_patch(diff_system)
    assert res_system.action == "ESCALATE"
    assert any("os.system" in reason for reason in res_system.escalation_reasons)
    assert len(res_system.reasons) == 0

    # 3. subprocess.run() call
    diff_subproc = make_diff("src/runner.py", "subprocess.run(['pytest'])\n")
    res_subproc = PatchPolicyEnforcer.evaluate_patch(diff_subproc)
    assert res_subproc.action == "ESCALATE"
    assert any("subprocess.run" in reason for reason in res_subproc.escalation_reasons)
    assert len(res_subproc.reasons) == 0


def test_safe_patch_is_allowed():
    """Safe patch on application code is allowed without escalation."""
    diff_safe = make_diff("src/service.py", "fixed_count = original_count + 1\nreturn fixed_count\n")
    res_safe = PatchPolicyEnforcer.evaluate_patch(diff_safe)
    assert res_safe.action == "ALLOW"
    assert res_safe.is_allowed is True
    assert len(res_safe.reasons) == 0
    assert len(res_safe.escalation_reasons) == 0
