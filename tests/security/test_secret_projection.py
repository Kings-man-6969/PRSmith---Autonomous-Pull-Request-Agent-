"""Security tests for immutable original snapshots and sanitized projections."""

from pathlib import Path
import pytest

from backend.security.secret_policy import (
    DepCacheKey,
    create_sanitized_projection,
    enforce_pre_llm_scan,
)


def test_sanitized_projection_does_not_modify_original_file(tmp_path: Path):
    # Setup test file with credentials
    secret_file = tmp_path / "service.py"
    raw_content = "API_KEY = 'sk-1234567890abcdef1234567890abcdef'\nPASSWORD = 'MySecretPassword123!'\n"
    secret_file.write_text(raw_content, encoding="utf-8")

    # Generate sanitized projection
    projection = create_sanitized_projection(tmp_path)

    # 1. Original file on disk MUST NOT be modified (Invariant 8)
    assert secret_file.read_text(encoding="utf-8") == raw_content

    # 2. Projection contains sanitized content with placeholders
    assert "service.py" in projection.sanitized_contents
    sanitized_text = projection.sanitized_contents["service.py"]
    assert "sk-1234567890abcdef" not in sanitized_text
    assert "[REDACTED:OPENAI_KEY]" in sanitized_text
    assert "[REDACTED:PASSWORD]" in sanitized_text

    # 3. Redaction report is accurate
    assert projection.total_redactions >= 2
    secret_types = {r.secret_type for r in projection.redaction_reports}
    assert "OPENAI_KEY" in secret_types
    assert "PASSWORD" in secret_types


def test_enforce_pre_llm_scan():
    raw_prompt = "User asks about config with token ghp_1234567890abcdef1234567890abcdef1234"
    cleaned = enforce_pre_llm_scan(raw_prompt)
    assert "ghp_1234567890abcdef" not in cleaned
    assert "[REDACTED:GITHUB_TOKEN]" in cleaned


def test_dep_cache_key_deterministic_and_independent_of_metadata():
    lock_a = b"package==1.0.0\ndependency==2.0.0\n"
    lock_b = b"package==1.0.1\ndependency==2.0.0\n"

    key_a1 = DepCacheKey.from_lockfile_content(lock_a, runtime_version="python3.11.7")
    key_a2 = DepCacheKey.from_lockfile_content(lock_a, runtime_version="python3.11.7")
    key_b = DepCacheKey.from_lockfile_content(lock_b, runtime_version="python3.11.7")

    assert key_a1.cache_identifier == key_a2.cache_identifier
    assert key_a1.cache_identifier != key_b.cache_identifier
    assert "python3.11.7" in key_a1.cache_identifier
    assert key_a1.lockfile_hash == key_a2.lockfile_hash
