"""Ephemeral read-only sanitized projections of repository file content.

Security invariant (mandatory — not optional):
    No raw repository-derived string may enter persistent graph/vector storage,
    vector embeddings, or an LLM prompt without passing this sanitization policy.

String classification:
    file_content    → full secret scan + redaction (redact_secrets)
    diff_text       → full secret scan + redaction (sanitize_diff)
    file_paths      → policy scan: flag sensitive path patterns
    symbol_names    → policy scan: warn on secret-like names
    commit_messages → policy scan + redaction
    exception text  → redaction before LLM / logs (sanitize_exception)
"""

import re
from pathlib import Path
from typing import List, Optional

from backend.observability.logging import get_logger
from backend.security.redaction import redact_secrets

logger = get_logger(__name__)


# Sensitive file path patterns — never embed these in LLM prompts
_SENSITIVE_PATH_PATTERNS: List[re.Pattern] = [
    re.compile(r"\.(pem|key|p12|pfx|crt|der|jks|keystore)$", re.IGNORECASE),
    re.compile(r"(secret|password|credential|token|apikey|api_key|private_key)", re.IGNORECASE),
    re.compile(r"\.(env|env\.local|env\.production|env\.staging)$", re.IGNORECASE),
    re.compile(r"(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.pub)?$", re.IGNORECASE),
    re.compile(r"(vault|secrets?)\.(json|yaml|yml|toml)$", re.IGNORECASE),
]

# Symbol name patterns that suggest accidental secret storage in code
_SENSITIVE_SYMBOL_PATTERNS: List[re.Pattern] = [
    re.compile(r"(password|secret|token|api_?key|private_?key|credentials?)", re.IGNORECASE),
]

# Diff hunk secret scanning: strip secrets from unified diff output
_DIFF_HUNK_PATTERN = re.compile(r"^[+\-](.*)$", re.MULTILINE)


class PathScanResult:
    __slots__ = ("path", "is_sensitive", "reason")

    def __init__(self, path: str, is_sensitive: bool, reason: str = "") -> None:
        self.path = path
        self.is_sensitive = is_sensitive
        self.reason = reason

    def __bool__(self) -> bool:
        return not self.is_sensitive


class SanitizedProjection:
    """Wraps repository content access with mandatory secret redaction.

    All methods that return strings guarantee that the output has been passed
    through the secret scanner. Callers MUST use this class instead of
    reading files directly with open().
    """

    def read_file_sanitized(self, clone_path: Path, rel_path: str) -> str:
        """Read a repository file and return its contents with secrets redacted.

        Returns empty string (with a logged warning) for sensitive file paths
        that should not be embedded at all.
        """
        scan = self.scan_file_path(rel_path)
        if scan.is_sensitive:
            logger.warning(
                "Suppressing sensitive file from LLM context",
                path=rel_path,
                reason=scan.reason,
            )
            return f"# [SUPPRESSED: sensitive file path — {scan.reason}]"

        full_path = clone_path / rel_path
        try:
            raw = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("Could not read file for sanitization", path=rel_path, error=str(e))
            return ""

        return redact_secrets(raw)

    def read_lines_sanitized(
        self,
        clone_path: Path,
        rel_path: str,
        start_line: int,
        end_line: int,
    ) -> str:
        """Read a specific line range from a file with secrets redacted.

        Line numbers are 1-indexed and inclusive.
        """
        scan = self.scan_file_path(rel_path)
        if scan.is_sensitive:
            logger.warning(
                "Suppressing sensitive file lines from LLM context",
                path=rel_path,
                reason=scan.reason,
            )
            return f"# [SUPPRESSED: {scan.reason}]"

        full_path = clone_path / rel_path
        try:
            all_lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError as e:
            logger.warning("Could not read file lines for sanitization", path=rel_path, error=str(e))
            return ""

        s = max(0, start_line - 1)
        e = min(len(all_lines), end_line)
        raw_slice = "".join(all_lines[s:e])
        return redact_secrets(raw_slice)

    def sanitize_diff(self, diff_text: str) -> str:
        """Redact secrets from a unified diff before LLM / storage consumption.

        Only diff hunk lines (+ / -) are passed through the secret scanner to
        avoid false positives on context lines or metadata headers.
        """
        def _redact_hunk_line(match: re.Match) -> str:
            prefix = match.group(0)[0]  # '+' or '-'
            content = match.group(1)
            return prefix + redact_secrets(content)

        return _DIFF_HUNK_PATTERN.sub(_redact_hunk_line, diff_text)

    def sanitize_exception(self, exc: Exception) -> str:
        """Redact secrets from an exception message before logging or LLM use."""
        return redact_secrets(str(exc))

    def scan_file_path(self, rel_path: str) -> PathScanResult:
        """Check whether a file path matches known sensitive patterns.

        Returns PathScanResult where .is_sensitive=True means the file should
        NOT be embedded in graph storage or LLM prompts.
        """
        for pattern in _SENSITIVE_PATH_PATTERNS:
            if pattern.search(rel_path):
                return PathScanResult(rel_path, is_sensitive=True, reason=f"path matches {pattern.pattern!r}")
        return PathScanResult(rel_path, is_sensitive=False)

    def scan_symbol_name(self, symbol_name: Optional[str]) -> PathScanResult:
        """Check whether a symbol name suggests sensitive data (e.g. stored secrets in code).

        Returns PathScanResult — is_sensitive=True is a WARNING, not a hard block.
        Symbol names are not suppressed, just flagged in logs.
        """
        if not symbol_name:
            return PathScanResult("", is_sensitive=False)
        for pattern in _SENSITIVE_SYMBOL_PATTERNS:
            if pattern.search(symbol_name):
                return PathScanResult(symbol_name, is_sensitive=True, reason=f"symbol name matches {pattern.pattern!r}")
        return PathScanResult(symbol_name, is_sensitive=False)


# Module-level singleton — use this everywhere instead of constructing per-call
_projection = SanitizedProjection()


def get_sanitized_projection() -> SanitizedProjection:
    """Return the module-level SanitizedProjection singleton."""
    return _projection
