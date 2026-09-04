"""Secret handling policy, immutable original snapshot protection, and dependency cache keys.

Enforces:
1. Original repository snapshots remain strictly immutable on disk.
2. Sanitized projections are derived for Knowledge Graph, Code Embeddings, and LLM reasoning.
3. Pre-LLM scan pass ensures no raw credentials reach model providers.
4. Dependency cache keys are derived strictly from cryptographic lockfile hashes,
   preventing PR-controlled metadata from polluting trusted validation caches.
"""

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.security.redaction import redact_secrets
from backend.security.secrets import SecretScanner


@dataclass
class RedactionReportEntry:
    file_path: str
    secret_type: str
    count: int


@dataclass
class SnapshotProjection:
    """Sanitized view of a repository snapshot for graph, vector, and LLM processing.

    The original snapshot directory is never modified.
    """
    original_path: Path
    sanitized_contents: Dict[str, str] = field(default_factory=dict)
    redaction_reports: List[RedactionReportEntry] = field(default_factory=list)

    @property
    def total_redactions(self) -> int:
        return sum(r.count for r in self.redaction_reports)


def create_sanitized_projection(
    snapshot_path: Path, max_file_size_bytes: int = 1_000_000
) -> SnapshotProjection:
    """Scan and redact secrets from snapshot files without modifying the original files."""
    projection = SnapshotProjection(original_path=snapshot_path)

    if not snapshot_path.exists():
        return projection

    for file_path in snapshot_path.rglob("*"):
        if not file_path.is_file():
            continue
        # Skip hidden git internals
        if ".git" in file_path.parts:
            continue
        if file_path.stat().st_size > max_file_size_bytes:
            continue

        rel_path = str(file_path.relative_to(snapshot_path)).replace("\\", "/")
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # Scan for secrets
        found = SecretScanner.scan(content)
        if found:
            # Group counts by secret type
            type_counts: Dict[str, int] = {}
            for secret_type, _, _, _ in found:
                type_counts[secret_type] = type_counts.get(secret_type, 0) + 1

            for stype, count in type_counts.items():
                projection.redaction_reports.append(
                    RedactionReportEntry(file_path=rel_path, secret_type=stype, count=count)
                )

            # Redact content for downstream use
            sanitized = redact_secrets(content)
            projection.sanitized_contents[rel_path] = sanitized
        else:
            projection.sanitized_contents[rel_path] = content

    return projection


def enforce_pre_llm_scan(context_text: str) -> str:
    """Mandatory defense-in-depth sanitization pass immediately before sending text to any LLM."""
    return redact_secrets(context_text)


@dataclass(frozen=True)
class DepCacheKey:
    """Cryptographically derived key for trusted dependency cache lookups.

    Key is derived strictly from content hash of the lockfile, language runtime,
    and platform architecture. PR title, description, or manifest names cannot
    influence this key.
    """
    lockfile_hash: str
    runtime_version: str
    platform: str
    ecosystem: str

    @classmethod
    def from_lockfile_content(
        cls,
        lockfile_bytes: bytes,
        runtime_version: str,
        platform: str = "linux/amd64",
        ecosystem: str = "pip",
    ) -> "DepCacheKey":
        digest = hashlib.sha256(lockfile_bytes).hexdigest()
        return cls(
            lockfile_hash=digest,
            runtime_version=runtime_version,
            platform=platform,
            ecosystem=ecosystem,
        )

    @property
    def cache_identifier(self) -> str:
        """Safe directory name for caching."""
        safe_runtime = self.runtime_version.replace("/", "_").replace(":", "_")
        safe_platform = self.platform.replace("/", "_")
        return f"{self.ecosystem}_{safe_runtime}_{safe_platform}_{self.lockfile_hash[:16]}"
