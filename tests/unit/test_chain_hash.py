"""Unit tests for cryptographic chain_hash binding pipeline lineage."""

import pytest
from backend.orchestration.hashing import canonical_hash, compute_chain_hash


def test_chain_hash_sensitivity():
    base_args = {
        "snapshot_sha": "a" * 40,
        "graph_content_hash": "b" * 64,
        "finding_content_hashes": ["f1" * 32, "f2" * 32],
        "patch_artifact_hash": "p" * 64,
        "validation_result_hash": "v" * 64,
        "pipeline_version": "v2",
    }
    base_hash = compute_chain_hash(**base_args)

    # 1. Snapshot SHA change alters chain_hash
    args_sha = dict(base_args, snapshot_sha="c" * 40)
    assert compute_chain_hash(**args_sha) != base_hash

    # 2. Graph content hash change alters chain_hash
    args_graph = dict(base_args, graph_content_hash="d" * 64)
    assert compute_chain_hash(**args_graph) != base_hash

    # 3. Finding hashes change alters chain_hash
    args_findings = dict(base_args, finding_content_hashes=["f1" * 32])
    assert compute_chain_hash(**args_findings) != base_hash

    # 4. Patch artifact hash change alters chain_hash
    args_patch = dict(base_args, patch_artifact_hash="p2" * 32)
    assert compute_chain_hash(**args_patch) != base_hash

    # 5. Validation result hash change alters chain_hash
    args_val = dict(base_args, validation_result_hash="v2" * 32)
    assert compute_chain_hash(**args_val) != base_hash

    # 6. Pipeline version change alters chain_hash
    args_ver = dict(base_args, pipeline_version="v3")
    assert compute_chain_hash(**args_ver) != base_hash


def test_chain_hash_supports_review_only_path():
    args_review_only = {
        "snapshot_sha": "a" * 40,
        "graph_content_hash": "b" * 64,
        "finding_content_hashes": ["f1" * 32],
        "patch_artifact_hash": None,
        "validation_result_hash": "v" * 64,
        "pipeline_version": "v2",
    }
    h1 = compute_chain_hash(**args_review_only)
    assert isinstance(h1, str)
    assert len(h1) == 64

    # Identical inputs yield identical chain_hash
    h2 = compute_chain_hash(**args_review_only)
    assert h1 == h2


def test_idempotency_key_derivation_from_chain_hash():
    chain_hash = compute_chain_hash(
        snapshot_sha="head123",
        graph_content_hash="graph123",
        finding_content_hashes=["find1"],
        patch_artifact_hash=None,
        validation_result_hash="val123",
        pipeline_version="v2",
    )
    repo_id = "repo-xyz"
    pr_number = 42
    idempotency_key = f"pub:{repo_id}:{pr_number}:{chain_hash}"

    assert idempotency_key.startswith("pub:repo-xyz:42:")
    assert chain_hash in idempotency_key
