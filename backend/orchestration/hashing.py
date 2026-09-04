"""Canonical hashing and artifact chain digest computation.

Enforces:
1. Deterministic canonical serialization (sorted keys, no timestamps or mutable IDs,
   UTF-8, normalized \\n line endings).
2. Cryptographic binding of the full pipeline lineage:
   snapshot_sha -> graph_content_hash -> finding_content_hashes -> patch_artifact_hash
   -> validation_result_hash -> pipeline_version -> chain_hash.
"""

import hashlib
import json
from typing import Any, Dict, List, Optional


def canonical_hash(content: Any) -> str:
    """Compute deterministic SHA-256 hash using canonical formatting.

    Rules:
    - Dicts: JSON-serialized with keys sorted recursively, no space after separators, ensure_ascii=False
    - Strings: Line endings normalized to \\n
    - Floats/Ints/Bools: converted deterministically
    """
    if isinstance(content, dict):
        normalized_dict = _strip_transient_fields(content)
        s = json.dumps(normalized_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    elif isinstance(content, (list, tuple)):
        normalized_list = [_strip_transient_fields(item) if isinstance(item, dict) else item for item in content]
        s = json.dumps(normalized_list, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    else:
        s = str(content)

    normalized_str = s.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized_str.encode("utf-8")).hexdigest()


def _strip_transient_fields(obj: Any) -> Any:
    """Recursively strip database IDs, timestamps, and transient fields from dictionaries."""
    if not isinstance(obj, dict):
        return obj

    transient_keys = {
        "id",
        "created_at",
        "updated_at",
        "completed_at",
        "posted_at",
        "claimed_at",
        "last_verified",
        "worker_id",
        "claimed_by",
        "attempts",
        "next_attempt_at",
    }
    cleaned = {}
    for k, v in obj.items():
        if k in transient_keys:
            continue
        if isinstance(v, dict):
            cleaned[k] = _strip_transient_fields(v)
        elif isinstance(v, list):
            cleaned[k] = [_strip_transient_fields(elem) if isinstance(elem, dict) else elem for elem in v]
        else:
            cleaned[k] = v
    return cleaned


def compute_chain_hash(
    snapshot_sha: str,
    graph_content_hash: str,
    finding_content_hashes: List[str],
    patch_artifact_hash: Optional[str],
    validation_result_hash: str,
    pipeline_version: str,
) -> str:
    """Compute cryptographic chain digest binding the complete pipeline execution lineage.

    Any modification to the input commit, the AST graph, the findings, the generated patch,
    the validation run outputs, or the pipeline engine version produces a distinct chain_hash.
    """
    payload = {
        "snapshot_sha": snapshot_sha.strip().lower(),
        "graph_content_hash": graph_content_hash.strip().lower(),
        "finding_content_hashes": sorted([h.strip().lower() for h in finding_content_hashes]),
        "patch_artifact_hash": (patch_artifact_hash or "").strip().lower(),
        "validation_result_hash": validation_result_hash.strip().lower(),
        "pipeline_version": pipeline_version.strip(),
    }
    return canonical_hash(payload)
