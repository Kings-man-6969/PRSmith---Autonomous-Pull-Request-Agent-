"""Pre-application patch scope, categorized denylist, and AST escalation policy enforcement."""

import ast
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from unidiff import PatchSet

from backend.config import settings
from backend.observability.logging import get_logger
from backend.observability.metrics import MetricsTracker
from backend.review.exceptions import DenylistViolation

logger = get_logger(__name__)


# Categorized denylist definition
CATEGORY_SECRETS = "SECRETS"
CATEGORY_CI_WORKFLOWS = "CI_WORKFLOWS"
CATEGORY_INFRASTRUCTURE = "INFRASTRUCTURE"
CATEGORY_DEPENDENCIES = "DEPENDENCIES"

CATEGORIZED_DENYLIST: Dict[str, List[str]] = {
    CATEGORY_SECRETS: [
        r"\.pem$",
        r"\.key$",
        r"id_rsa",
        r"^\.?\.env.*",
        r"\.p12$",
        r"\.pfx$",
        r"credentials\.json$",
    ],
    CATEGORY_CI_WORKFLOWS: [
        r"^\.?\.github/workflows/",
        r"^\.?\.gitlab-ci\.ya?ml$",
        r"^\.?azure-pipelines\.ya?ml$",
        r"^\.?Jenkinsfile",
        r"^\.?\.circleci/",
    ],
    CATEGORY_INFRASTRUCTURE: [
        r"^Dockerfile.*",
        r"^docker-compose.*\.ya?ml$",
        r"^\.?\.k8s/",
        r"^kubernetes/",
        r"^helm/",
        r"^charts/",
        r"^terraform/",
    ],
    CATEGORY_DEPENDENCIES: [
        r"^requirements.*\.txt$",
        r"^pyproject\.toml$",
        r"^package\.json$",
        r"^package-lock\.json$",
        r"^yarn\.lock$",
        r"^pnpm-lock\.yaml$",
        r"^Cargo\.lock$",
        r"^Cargo\.toml$",
        r"^Gemfile(\.lock)?$",
        r"^go\.(mod|sum)$",
    ],
}

TEST_PATTERNS = [
    r"^tests?/",
    r"^spec/",
    r"test_.*\.py$",
    r".*_test\.py$",
    r".*\.test\.(js|ts|jsx|tsx)$",
    r".*\.spec\.(js|ts|jsx|tsx)$",
]

# Suspicious AST call patterns that warrant human escalation rather than outright rejection
SENSITIVE_CALL_NAMES: Set[str] = {
    "eval",
    "exec",
    "__import__",
    "compile",
}

SENSITIVE_ATTRIBUTE_CALLS: Set[str] = {
    "os.system",
    "os.popen",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.check_call",
    "pickle.loads",
    "yaml.unsafe_load",
}


def classify_yaml_path(file_path: str) -> str:
    """Semantically classify YAML files as INFRASTRUCTURE vs APPLICATION.

    Only INFRASTRUCTURE YAML (CI, deployment, kubernetes, containers) is denied.
    APPLICATION YAML (OpenAPI, docs, translations, fixtures) is permitted.
    """
    path_normalized = file_path.replace("\\", "/").lower()
    infra_markers = [
        ".github/",
        ".gitlab/",
        ".circleci/",
        "k8s/",
        "kubernetes/",
        "helm/",
        "charts/",
        "deploy/",
        "docker-compose",
        "ansible/",
        "terraform/",
    ]
    for marker in infra_markers:
        if marker in path_normalized:
            return "INFRASTRUCTURE"

    return "APPLICATION"


@dataclass
class PatchPolicyResult:
    """Outcome of evaluating a patch against safety and escalation policies."""

    action: str  # "ALLOW" | "REJECT" | "ESCALATE"
    is_allowed: bool
    reasons: List[str] = field(default_factory=list)
    escalation_reasons: List[str] = field(default_factory=list)
    category_violations: Dict[str, List[str]] = field(default_factory=dict)
    files_changed: List[str] = field(default_factory=list)
    lines_added: int = 0
    lines_deleted: int = 0
    total_bytes: int = 0


class PatchPolicyEnforcer:
    """Enforces boundaries, categorized denylists, and AST escalation on patches."""

    @classmethod
    def check_ast_escalations(cls, added_python_code: str) -> List[str]:
        """Inspect added Python code for patterns requiring human review/escalation."""
        escalations: List[str] = []
        if not added_python_code.strip():
            return escalations

        # Attempt full AST parse by wrapping in dummy function
        try:
            tree = ast.parse(added_python_code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Direct call e.g. eval(...)
                    if isinstance(node.func, ast.Name) and node.func.id in SENSITIVE_CALL_NAMES:
                        escalations.append(f"Added call to dynamic code execution: '{node.func.id}()'")
                    # Attribute call e.g. os.system(...) or subprocess.run(...)
                    elif isinstance(node.func, ast.Attribute):
                        parts = []
                        curr = node.func
                        while isinstance(curr, ast.Attribute):
                            parts.append(curr.attr)
                            curr = curr.value
                        if isinstance(curr, ast.Name):
                            parts.append(curr.id)
                            attr_call = ".".join(reversed(parts))
                            if attr_call in SENSITIVE_ATTRIBUTE_CALLS:
                                escalations.append(f"Added system execution call: '{attr_call}()'")
        except SyntaxError:
            # Fallback regex search if diff fragment isn't standalone valid syntax
            for name in SENSITIVE_CALL_NAMES:
                if re.search(rf"\b{name}\s*\(", added_python_code):
                    escalations.append(f"Potential dynamic execution call detected: '{name}()'")
            for attr_call in SENSITIVE_ATTRIBUTE_CALLS:
                escaped = re.escape(attr_call)
                if re.search(rf"\b{escaped}\s*\(", added_python_code):
                    escalations.append(f"Potential system execution call detected: '{attr_call}()'")

        return list(dict.fromkeys(escalations))

    @classmethod
    def evaluate_patch(
        cls,
        patch_diff: str,
        allow_test_modification: bool = False,
        allowed_file_scope: Optional[List[str]] = None,
    ) -> PatchPolicyResult:
        """Evaluate patch for size, categorized denylists, test modification, and AST escalation."""
        reasons: List[str] = []
        escalation_reasons: List[str] = []
        category_violations: Dict[str, List[str]] = {}

        if not patch_diff.strip():
            return PatchPolicyResult(action="REJECT", is_allowed=False, reasons=["Patch is empty."])

        total_bytes = len(patch_diff.encode("utf-8"))
        if total_bytes > settings.MAX_PATCH_BYTES:
            reasons.append(f"Patch size ({total_bytes} bytes) exceeds limit ({settings.MAX_PATCH_BYTES} bytes).")

        files_changed: List[str] = []
        lines_added = 0
        lines_deleted = 0
        added_python_lines: List[str] = []

        try:
            patch_set = PatchSet(patch_diff)
            for patched_file in patch_set:
                file_path = patched_file.path
                if file_path.startswith("a/") or file_path.startswith("b/"):
                    file_path = file_path[2:]
                files_changed.append(file_path)
                lines_added += patched_file.added
                lines_deleted += patched_file.removed

                if file_path.endswith(".py"):
                    for hunk in patched_file:
                        for line in hunk:
                            if line.is_added:
                                added_python_lines.append(line.value)
        except Exception:
            extracted_files: Set[str] = set()
            for line in patch_diff.splitlines():
                if line.startswith("--- a/") or line.startswith("+++ b/"):
                    extracted_files.add(line[6:].strip())
                elif line.startswith("--- ") or line.startswith("+++ "):
                    cleaned = line[4:].strip()
                    if cleaned.startswith("a/") or cleaned.startswith("b/"):
                        cleaned = cleaned[2:]
                    if cleaned and cleaned != "/dev/null":
                        extracted_files.add(cleaned)
                elif line.startswith("+") and not line.startswith("+++"):
                    lines_added += 1
                    added_python_lines.append(line[1:])
                elif line.startswith("-") and not line.startswith("---"):
                    lines_deleted += 1

            files_changed = list(extracted_files)

        if not files_changed:
            return PatchPolicyResult(
                action="REJECT",
                is_allowed=False,
                reasons=["Invalid unified diff format: no file headers found"],
                total_bytes=total_bytes,
            )

        # 1. Categorized denylist checks
        for file_path in files_changed:
            # Check categorized denylists
            for category, patterns in CATEGORIZED_DENYLIST.items():
                for pattern in patterns:
                    if re.search(pattern, file_path, re.IGNORECASE):
                        # Special handling for YAML: check semantic classification
                        if file_path.endswith((".yaml", ".yml")):
                            classification = classify_yaml_path(file_path)
                            if classification == "APPLICATION":
                                continue  # Allow application YAML!

                        reasons.append(f"Modifying {category} file '{file_path}' is forbidden by policy.")
                        category_violations.setdefault(category, []).append(file_path)
                        break

            # 2. Check unauthorized test modifications
            if not allow_test_modification:
                for pattern in TEST_PATTERNS:
                    if re.search(pattern, file_path, re.IGNORECASE):
                        reasons.append(
                            f"Modifying test file '{file_path}' is forbidden without explicit authorization."
                        )
                        category_violations.setdefault("TESTS", []).append(file_path)
                        break

            # 3. Check allowed file scope
            if allowed_file_scope and file_path not in allowed_file_scope:
                reasons.append(f"File '{file_path}' is outside the authorized impact scope.")

        # 4. Limits checks
        if len(files_changed) > settings.MAX_FILES_CHANGED:
            reasons.append(f"Changed {len(files_changed)} files (limit is {settings.MAX_FILES_CHANGED}).")

        if lines_added > settings.MAX_LINES_ADDED:
            reasons.append(f"Added {lines_added} lines (limit is {settings.MAX_LINES_ADDED}).")

        if lines_deleted > settings.MAX_LINES_DELETED:
            reasons.append(f"Deleted {lines_deleted} lines (limit is {settings.MAX_LINES_DELETED}).")

        # 5. AST escalation checks on added Python lines
        if added_python_lines:
            escalations = cls.check_ast_escalations("\n".join(added_python_lines))
            if escalations:
                escalation_reasons.extend(escalations)

        # Determine overall action
        if reasons:
            action = "REJECT"
            is_allowed = False
            for r in reasons:
                MetricsTracker.record_patch_rejected(r)
                logger.warning("Patch rejected by policy enforcer", reason=r)
        elif escalation_reasons:
            action = "ESCALATE"
            is_allowed = False  # Requires human review before auto-applying
            logger.info("Patch flagged for human escalation", reasons=escalation_reasons)
        else:
            action = "ALLOW"
            is_allowed = True

        return PatchPolicyResult(
            action=action,
            is_allowed=is_allowed,
            reasons=reasons,
            escalation_reasons=escalation_reasons,
            category_violations=category_violations,
            files_changed=files_changed,
            lines_added=lines_added,
            lines_deleted=lines_deleted,
            total_bytes=total_bytes,
        )

    @classmethod
    def assert_patch_allowed(cls, result: PatchPolicyResult) -> None:
        """Enforce that a patch evaluation passed; raises DenylistViolation if rejected."""
        if result.action == "REJECT":
            raise DenylistViolation("; ".join(result.reasons))
