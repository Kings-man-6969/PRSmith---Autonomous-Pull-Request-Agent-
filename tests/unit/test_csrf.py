"""Unit tests for double-submit cookie CSRF protection."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.security.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    generate_csrf_token,
    verify_csrf,
)


def make_request(cookies=None, headers=None) -> Request:
    """Helper to build a Starlette Request with specific cookies and headers."""
    cookie_dict = cookies or {}
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    raw_headers = []
    if cookie_str:
        raw_headers.append((b"cookie", cookie_str.encode()))
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode(), v.encode()))

    scope = {
        "type": "http",
        "method": "POST",
        "headers": raw_headers,
        "path": "/api/v1/jobs/trigger",
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_csrf_missing_both_raises_403():
    """Request missing both cookie and header fails with 403."""
    req = make_request()
    with pytest.raises(HTTPException) as exc:
        await verify_csrf(req)
    assert exc.value.status_code == 403
    assert "Missing both" in exc.value.detail


@pytest.mark.asyncio
async def test_csrf_cookie_only_raises_403():
    """Request with cookie but missing X-CSRF-Token header fails with 403."""
    token = generate_csrf_token()
    req = make_request(cookies={CSRF_COOKIE_NAME: token})

    with pytest.raises(HTTPException) as exc:
        await verify_csrf(req)
    assert exc.value.status_code == 403
    assert "Missing X-CSRF-Token header" in exc.value.detail


@pytest.mark.asyncio
async def test_csrf_header_only_raises_403():
    """Request with X-CSRF-Token header but missing cookie fails with 403."""
    token = generate_csrf_token()
    req = make_request(headers={CSRF_HEADER_NAME: token})

    with pytest.raises(HTTPException) as exc:
        await verify_csrf(req)
    assert exc.value.status_code == 403
    assert "Missing CSRF cookie" in exc.value.detail


@pytest.mark.asyncio
async def test_csrf_token_mismatch_raises_403():
    """Request where cookie token and header token do not match fails with 403."""
    cookie_token = generate_csrf_token()
    header_token = generate_csrf_token()
    req = make_request(
        cookies={CSRF_COOKIE_NAME: cookie_token},
        headers={CSRF_HEADER_NAME: header_token},
    )

    with pytest.raises(HTTPException) as exc:
        await verify_csrf(req)
    assert exc.value.status_code == 403
    assert "Token mismatch" in exc.value.detail


@pytest.mark.asyncio
async def test_csrf_valid_double_submit_succeeds():
    """Request with matching cookie and X-CSRF-Token header succeeds."""
    token = generate_csrf_token()
    req = make_request(
        cookies={CSRF_COOKIE_NAME: token},
        headers={CSRF_HEADER_NAME: token},
    )

    verified = await verify_csrf(req)
    assert verified == token
