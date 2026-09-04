from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from cryptography.fernet import Fernet

from backend.auth.credential_provider import (
    CredentialExpiredError,
    GitHubCredentialProvider,
)
from backend.auth.token_encryption import encrypt_token
from backend.config import settings
from backend.database.models import GitHubConnection


@pytest.fixture(autouse=True)
def setup_encryption(monkeypatch):
    test_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "GITHUB_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setattr(settings, "GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GITHUB_CLIENT_SECRET", "test-client-secret")


@pytest.mark.asyncio
async def test_get_user_credential_no_expiry():
    provider = GitHubCredentialProvider()
    user_id = "user-classic"
    plain_token = "gho_classicToken123"

    conn = GitHubConnection(
        user_id=user_id,
        access_token_encrypted=encrypt_token(plain_token),
        access_token_expires_at=None,
    )
    session = AsyncMock()
    session.bind.dialect.name = "sqlite"
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = conn
    session.execute.return_value = mock_res

    resolved = await provider.get_user_credential(user_id, session)
    assert resolved == plain_token


@pytest.mark.asyncio
async def test_get_user_credential_valid_future_expiry():
    provider = GitHubCredentialProvider()
    user_id = "user-valid"
    plain_token = "gho_futureValidToken123"
    future_time = datetime.now(timezone.utc) + timedelta(hours=2)

    conn = GitHubConnection(
        user_id=user_id,
        access_token_encrypted=encrypt_token(plain_token),
        access_token_expires_at=future_time,
    )
    session = AsyncMock()
    session.bind.dialect.name = "sqlite"
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = conn
    session.execute.return_value = mock_res

    resolved = await provider.get_user_credential(user_id, session)
    assert resolved == plain_token


@pytest.mark.asyncio
async def test_get_user_credential_refreshes_when_expired():
    provider = GitHubCredentialProvider()
    user_id = "user-refreshing"
    old_token = "gho_oldExpiredToken"
    refresh_token = "ghr_validRefreshToken"
    past_time = datetime.now(timezone.utc) - timedelta(minutes=10)

    conn = GitHubConnection(
        user_id=user_id,
        access_token_encrypted=encrypt_token(old_token),
        refresh_token_encrypted=encrypt_token(refresh_token),
        access_token_expires_at=past_time,
    )
    session = AsyncMock()
    session.bind.dialect.name = "sqlite"
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = conn
    session.execute.return_value = mock_res

    new_token = "gho_newRefreshedAccessToken"
    new_refresh = "ghr_newRotatedRefreshToken"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": new_token,
        "refresh_token": new_refresh,
        "expires_in": 28800,
        "refresh_token_expires_in": 15811200,
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        resolved = await provider.get_user_credential(user_id, session)

    assert resolved == new_token
    # Verify connection was updated and session committed
    session.commit.assert_awaited_once()
    assert conn.access_token_expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_get_user_credential_raises_when_expired_and_no_refresh():
    provider = GitHubCredentialProvider()
    user_id = "user-dead"
    old_token = "gho_oldExpiredToken"
    past_time = datetime.now(timezone.utc) - timedelta(minutes=10)

    conn = GitHubConnection(
        user_id=user_id,
        access_token_encrypted=encrypt_token(old_token),
        refresh_token_encrypted=None,
        access_token_expires_at=past_time,
    )
    session = AsyncMock()
    session.bind.dialect.name = "sqlite"
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = conn
    session.execute.return_value = mock_res

    with pytest.raises(CredentialExpiredError, match="no refresh token available"):
        await provider.get_user_credential(user_id, session)


def test_get_dev_pat_returns_none_by_default(monkeypatch):
    provider = GitHubCredentialProvider()
    monkeypatch.setattr(settings, "ENABLE_PAT_FALLBACK", False)
    monkeypatch.setattr(settings, "GITHUB_PAT", "ghp_devPat123")
    assert provider.get_dev_pat() is None


def test_get_dev_pat_returns_token_when_enabled(monkeypatch):
    provider = GitHubCredentialProvider()
    monkeypatch.setattr(settings, "ENABLE_PAT_FALLBACK", True)
    monkeypatch.setattr(settings, "GITHUB_PAT", "ghp_devPat123")
    assert provider.get_dev_pat() == "ghp_devPat123"
