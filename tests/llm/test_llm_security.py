"""Security tests: SSRF boundary validation, credential masking, and header sanitization."""

import pytest
from backend.llm.errors import SSRFBlockedError
from backend.llm.security import SSRFValidator, mask_secret, sanitize_headers


def test_ssrf_blocks_loopback_and_metadata_addresses():
    blocked_urls = [
        "http://127.0.0.1:8000/v1",
        "http://localhost:11434/v1",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1:8080/v1",
        "http://192.168.1.1:5000/v1",
        "http://172.16.0.1:8000/v1",
    ]
    for url in blocked_urls:
        with pytest.raises(SSRFBlockedError):
            SSRFValidator.validate_url(url, allow_local=False)


def test_ssrf_allows_localhost_when_explicitly_permitted():
    url = "http://localhost:11434/v1"
    valid = SSRFValidator.validate_url(url, allow_local=True)
    assert valid == "http://localhost:11434/v1"


def test_ssrf_rejects_invalid_schemes():
    with pytest.raises(SSRFBlockedError):
        SSRFValidator.validate_url("ftp://api.deepseek.com")
    with pytest.raises(SSRFBlockedError):
        SSRFValidator.validate_url("file:///etc/passwd")


def test_mask_secret_protects_credentials():
    assert mask_secret("sk-proj-1234567890abcdef") == "sk-***cdef"
    assert mask_secret("short") == "***"
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


def test_sanitize_headers_masks_sensitive_keys():
    headers = {
        "Authorization": "Bearer sk-1234567890",
        "x-api-key": "secret-api-key-12345",
        "Content-Type": "application/json",
        "User-Agent": "PRSmith-Agent/1.0",
    }
    sanitized = sanitize_headers(headers)
    assert "sk-" not in sanitized["Authorization"]
    assert "***" in sanitized["Authorization"]
    assert "***" in sanitized["x-api-key"]
    assert sanitized["Content-Type"] == "application/json"
    assert sanitized["User-Agent"] == "PRSmith-Agent/1.0"
