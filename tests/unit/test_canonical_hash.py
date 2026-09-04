"""Unit tests for canonical hashing and artifact chain digest computation."""

from backend.orchestration.hashing import canonical_hash, compute_chain_hash


def test_canonical_hash_key_order_invariance():
    dict1 = {"b": 2, "a": 1, "c": {"y": 20, "x": 10}}
    dict2 = {"a": 1, "c": {"x": 10, "y": 20}, "b": 2}
    assert canonical_hash(dict1) == canonical_hash(dict2)


def test_canonical_hash_strips_transient_database_fields():
    dict_clean = {"finding": "ISSUE-01", "severity": "HIGH", "file": "app.py"}
    dict_with_db = {
        "id": "uuid-123",
        "created_at": "2026-09-03T12:00:00Z",
        "worker_id": "worker-abc",
        "finding": "ISSUE-01",
        "severity": "HIGH",
        "file": "app.py",
    }
    assert canonical_hash(dict_clean) == canonical_hash(dict_with_db)


def test_canonical_hash_normalizes_line_endings():
    text_crlf = "def hello():\r\n    return 'world'\r\n"
    text_lf = "def hello():\n    return 'world'\n"
    assert canonical_hash(text_crlf) == canonical_hash(text_lf)


def test_compute_chain_hash_sensitivity():
    base_chain = compute_chain_hash(
        snapshot_sha="abc1234567890abcdef1234567890abcdef12345",
        graph_content_hash="graph_hash_1",
        finding_content_hashes=["finding_hash_1", "finding_hash_2"],
        patch_artifact_hash="patch_hash_1",
        validation_result_hash="val_hash_1",
        pipeline_version="20260903.1",
    )

    # 1. Same inputs -> identical chain hash
    same_chain = compute_chain_hash(
        snapshot_sha="ABC1234567890abcdef1234567890abcdef12345",  # case-insensitive
        graph_content_hash="graph_hash_1",
        finding_content_hashes=["finding_hash_2", "finding_hash_1"],  # order-insensitive
        patch_artifact_hash="patch_hash_1",
        validation_result_hash="val_hash_1",
        pipeline_version="20260903.1",
    )
    assert base_chain == same_chain

    # 2. Alter snapshot_sha -> different chain hash
    diff_sha = compute_chain_hash(
        snapshot_sha="fff1234567890abcdef1234567890abcdef12345",
        graph_content_hash="graph_hash_1",
        finding_content_hashes=["finding_hash_1"],
        patch_artifact_hash="patch_hash_1",
        validation_result_hash="val_hash_1",
        pipeline_version="20260903.1",
    )
    assert diff_sha != base_chain

    # 3. Alter patch hash -> different chain hash
    diff_patch = compute_chain_hash(
        snapshot_sha="abc1234567890abcdef1234567890abcdef12345",
        graph_content_hash="graph_hash_1",
        finding_content_hashes=["finding_hash_1", "finding_hash_2"],
        patch_artifact_hash="patch_hash_2",
        validation_result_hash="val_hash_1",
        pipeline_version="20260903.1",
    )
    assert diff_patch != base_chain
