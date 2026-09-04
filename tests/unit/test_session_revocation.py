"""Unit tests for JWT session versioning and revocation invariants."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.security import (
    COOKIE_SESSION_NAME,
    create_session_token,
    get_current_user,
    get_current_user_optional,
    revoke_user_sessions,
)
from backend.database.models import User


def make_mock_request(cookies=None, headers=None) -> Request:
    """Create a minimal Starlette request mock for dependency testing."""
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
        "method": "GET",
        "headers": raw_headers,
        "path": "/",
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_session_version_matches_authenticates(db_session: AsyncSession):
    """User with matching JWT session_version authenticates successfully."""
    user = User(
        github_id=12345,
        username="alice",
        role="developer",
        session_version=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    token = create_session_token(user_id=user.id, github_id=user.github_id, session_version=1)
    req = make_mock_request(cookies={COOKIE_SESSION_NAME: token})

    authenticated_user = await get_current_user(request=req, session=db_session)
    assert authenticated_user.id == user.id
    assert authenticated_user.username == "alice"


@pytest.mark.asyncio
async def test_session_version_mismatch_raises_401(db_session: AsyncSession):
    """Token with stale session_version is rejected with 401."""
    user = User(
        github_id=12346,
        username="bob",
        role="developer",
        session_version=2,  # Current DB session version is 2
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # Token issued when session_version was 1
    stale_token = create_session_token(user_id=user.id, github_id=user.github_id, session_version=1)
    req = make_mock_request(cookies={COOKIE_SESSION_NAME: stale_token})

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request=req, session=db_session)

    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_revoke_user_sessions_increments_version_and_invalidates_token(db_session: AsyncSession):
    """Calling revoke_user_sessions increments session_version and invalidates prior JWT."""
    user = User(
        github_id=12347,
        username="charlie",
        role="admin",
        session_version=1,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    token_v1 = create_session_token(user_id=user.id, github_id=user.github_id, session_version=1)
    req_v1 = make_mock_request(cookies={COOKIE_SESSION_NAME: token_v1})

    # Initially valid
    auth_user = await get_current_user(request=req_v1, session=db_session)
    assert auth_user.id == user.id

    # Revoke all sessions (e.g. on logout or password/credential reset)
    new_version = await revoke_user_sessions(session=db_session, user=user)
    assert new_version == 2
    assert user.session_version == 2

    # Old token is now rejected
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request=req_v1, session=db_session)
    assert exc_info.value.status_code == 401

    # New token with version 2 works
    token_v2 = create_session_token(user_id=user.id, github_id=user.github_id, session_version=2)
    req_v2 = make_mock_request(cookies={COOKIE_SESSION_NAME: token_v2})
    auth_user_v2 = await get_current_user(request=req_v2, session=db_session)
    assert auth_user_v2.id == user.id


@pytest.mark.asyncio
async def test_optional_user_returns_none_on_revoked_session(db_session: AsyncSession):
    """get_current_user_optional returns None rather than raising if session is revoked."""
    user = User(
        github_id=12348,
        username="dave",
        session_version=3,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    stale_token = create_session_token(user_id=user.id, github_id=user.github_id, session_version=2)
    req = make_mock_request(cookies={COOKIE_SESSION_NAME: stale_token})

    res = await get_current_user_optional(request=req, session=db_session)
    assert res is None
