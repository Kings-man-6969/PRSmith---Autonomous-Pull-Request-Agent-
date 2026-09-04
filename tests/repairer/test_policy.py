import pytest
from backend.repair.patch_policy import PatchPolicyEnforcer

VALID_PATCH = """--- a/service.py
+++ b/service.py
@@ -1,1 +1,1 @@
-def foo(): return False
+def foo(): return True
"""

FORBIDDEN_CI_PATCH = """--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -1,1 +1,1 @@
- run: pytest
+ run: echo bypass
"""

FORBIDDEN_TEST_PATCH = """--- a/tests/test_foo.py
+++ b/tests/test_foo.py
@@ -1,1 +1,1 @@
- assert x == 1
+ assert True
"""


def test_valid_patch_allowed():
    res = PatchPolicyEnforcer.evaluate_patch(VALID_PATCH)
    assert res.is_allowed is True
    assert res.lines_added == 1
    assert res.lines_deleted == 1
    assert "service.py" in res.files_changed


def test_ci_patch_rejected():
    res = PatchPolicyEnforcer.evaluate_patch(FORBIDDEN_CI_PATCH)
    assert res.is_allowed is False
    assert any("forbidden by policy" in r for r in res.reasons)


def test_unauthorized_test_patch_rejected():
    res = PatchPolicyEnforcer.evaluate_patch(FORBIDDEN_TEST_PATCH, allow_test_modification=False)
    assert res.is_allowed is False
    assert any("forbidden without explicit authorization" in r for r in res.reasons)
