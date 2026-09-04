import time
from unittest.mock import AsyncMock, MagicMock
import jwt
import pytest
from fastapi import HTTPException

from backend.auth.security import (
    COOKIE_SESSION_NAME,
    create_session_token,
    decode_session_token,
    generate_oauth_state,
    get_current_user,
    get_current_user_optional,
)
from backend.config import settings
from backend.database.models import User


def test_generate_oauth_state():
    state1 = generate_oauth_state()
    state2 = generate_oauth_state()
    assert isinstance(state1, str)
    assert len(state1) >= 32
    assert state1 != state2


def test_create_session_token_and_decode():
    user_id = "user-12345"
    github_id = 98765432
    token = create_session_token(user_id=user_id, github_id=github_id)
    assert isinstance(token, str)

    payload = decode_session_token(token)
    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["github_id"] == github_id
    assert "iat" in payload
    assert "exp" in payload
    assert payload["exp"] > payload["iat"]


def test_decode_session_token_rejects_expired():
    # Construct an expired token
    now = int(time.time())
    payload = {
        "sub": "user-expired",
        "github_id": 1234,
        "iat": now - 3600,
        "exp": now - 60,  # expired 60s ago
    }
    expired_token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    result = decode_session_token(expired_token)
    assert result is None


def test_decode_session_token_rejects_wrong_key():
    token = jwt.encode(
        {"sub": "user-hacker", "github_id": 999},
        "wrong-secret-key-123",
        algorithm="HS256",
    )
    result = decode_session_token(token)
    assert result is None


def test_decode_session_token_rejects_malformed():
    assert decode_session_token("not.a.valid.jwt") is None
    assert decode_session_token("") is None


@pytest.mark.asyncio
async def test_get_current_user_raises_401_when_cookie_missing():
    request = MagicMock()
    request.cookies = {}
    session = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await get_current_user(request=request, session=session)
    assert exc.value.status_code == 401
    assert "Authentication required" in exc.value.detail


@pytest.mark.asyncio
async def test_get_current_user_raises_401_when_token_invalid():
    request = MagicMock()
    request.cookies = {COOKIE_SESSION_NAME: "invalid-token"}
    session = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await get_current_user(request=request, session=session)
    assert exc.value.status_code == 401
    assert "Invalid or expired session" in exc.value.detail


@pytest.mark.asyncio
async def test_get_current_user_success():
    valid_token = create_session_token("user-valid-id", 4567)
    request = MagicMock()
    request.cookies = {COOKIE_SESSION_NAME: valid_token}

    user = User(id="user-valid-id", github_id=4567, username="testdev")
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = user
    session.execute.return_value = mock_result

    curr = await get_current_user(request=request, session=session)
    assert curr.id == "user-valid-id"
    assert curr.username == "testdev"


@pytest.mark.asyncio
async def test_get_current_user_optional_returns_none_when_unauthenticated():
    request = MagicMock()
    request.cookies = {}
    session = AsyncMock()

    curr = await get_current_user_optional(request=request, session=session)
    assert curr is None
