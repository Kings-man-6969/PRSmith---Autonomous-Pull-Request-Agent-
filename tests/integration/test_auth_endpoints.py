from unittest.mock import AsyncMock, patch
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
import httpx
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app import app
from backend.auth.security import COOKIE_OAUTH_STATE_NAME, COOKIE_SESSION_NAME, create_session_token
from backend.config import settings
from backend.database.models import Base, User
from backend.database.sessions import get_db_session


@pytest_asyncio.fixture
async def test_env(monkeypatch):
    test_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "GITHUB_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setattr(settings, "GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "FRONTEND_URL", "http://localhost:3000")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = override_get_db_session

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_github_login_redirect_and_state_cookie(test_env):
    client, _ = test_env
    res = await client.get("/api/auth/github/login", follow_redirects=False)

    assert res.status_code == 302
    location = res.headers.get("location", "")
    assert "https://github.com/login/oauth/authorize" in location
    assert "client_id=test-client-id" in location
    assert "state=" in location

    # Check that oauth_state cookie is set
    assert COOKIE_OAUTH_STATE_NAME in res.cookies
    cookie_state = res.cookies[COOKIE_OAUTH_STATE_NAME]
    assert cookie_state in location


@pytest.mark.asyncio
async def test_github_callback_missing_or_mismatched_state(test_env):
    client, _ = test_env

    # 1. No cookie at all
    res1 = await client.get("/api/auth/github/callback?code=abc&state=xyz")
    assert res1.status_code == 400
    assert "CSRF" in res1.json()["detail"]

    # 2. Mismatched state
    client.cookies.set(COOKIE_OAUTH_STATE_NAME, "expected_state")
    res2 = await client.get("/api/auth/github/callback?code=abc&state=wrong_state")
    assert res2.status_code == 400
    assert "CSRF" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_github_callback_success(test_env):
    client, session_factory = test_env
    state = "valid-state-123"
    client.cookies.set(COOKIE_OAUTH_STATE_NAME, state)

    mock_token_resp = httpx.Response(
        status_code=200,
        json={
            "access_token": "gho_test_user_token_abc",
            "scope": "read:user user:email read:org",
            "token_type": "bearer",
            "expires_in": 28800,
        },
    )
    mock_user_resp = httpx.Response(
        status_code=200,
        json={
            "id": 1234567,
            "login": "octocat-tester",
            "email": "octocat@github.com",
            "avatar_url": "https://avatars.githubusercontent.com/u/1234567",
        },
    )

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_token_resp
    mock_client_instance.get.return_value = mock_user_resp
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance.__aexit__.return_value = None

    with patch("backend.api.auth.httpx.AsyncClient", return_value=mock_client_instance):
        res = await client.get(f"/api/auth/github/callback?code=testcode123&state={state}", follow_redirects=False)

    assert res.status_code == 302
    assert res.headers["location"] == "http://localhost:3000/repos"
    assert COOKIE_SESSION_NAME in res.cookies

    # Verify user in database
    async with session_factory() as session:
        from sqlalchemy import select
        db_user = (await session.execute(select(User).where(User.github_id == 1234567))).scalars().first()
        assert db_user is not None
        assert db_user.username == "octocat-tester"
        assert db_user.email == "octocat@github.com"


@pytest.mark.asyncio
async def test_me_endpoint_authenticated_and_unauthenticated(test_env):
    client, session_factory = test_env

    # 1. Unauthenticated -> 401
    res_unauth = await client.get("/api/auth/me")
    assert res_unauth.status_code == 401

    # 2. Insert test user in DB
    async with session_factory() as session:
        user = User(github_id=99999, username="authed_user", email="auth@test.com")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    # Create session cookie
    token = create_session_token(user_id=user_id, github_id=99999)
    client.cookies.set(COOKIE_SESSION_NAME, token)

    res_auth = await client.get("/api/auth/me")
    assert res_auth.status_code == 200
    data = res_auth.json()
    assert data["username"] == "authed_user"
    assert data["github_id"] == 99999


@pytest.mark.asyncio
async def test_logout_endpoint(test_env):
    client, _ = test_env
    client.cookies.set(COOKIE_SESSION_NAME, "some-existing-session")

    res = await client.post("/api/auth/logout")
    assert res.status_code == 200
    assert res.json()["status"] == "logged_out"

    # Verify cookie cleared
    res_me = await client.get("/api/auth/me")
    assert res_me.status_code == 401
