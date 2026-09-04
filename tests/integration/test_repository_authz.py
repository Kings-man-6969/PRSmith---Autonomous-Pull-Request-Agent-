from unittest.mock import AsyncMock, patch
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
import httpx
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app import app
from backend.auth.security import COOKIE_SESSION_NAME, create_session_token
from backend.auth.token_encryption import encrypt_token
from backend.config import settings
from backend.database.models import Base, GitHubConnection, Job, Repository, User, UserRepository
from backend.database.sessions import get_db_session


@pytest_asyncio.fixture
async def authz_env(monkeypatch):
    test_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "GITHUB_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setattr(settings, "ENABLE_PAT_FALLBACK", False)
    monkeypatch.setattr(settings, "GITHUB_PAT", "")

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
async def test_unauthenticated_endpoints_return_401(authz_env):
    client, _ = authz_env
    # List repositories requires auth
    res1 = await client.get("/api/repositories")
    assert res1.status_code == 401

    # Discover without auth and with ENABLE_PAT_FALLBACK=false returns 401
    res2 = await client.get("/api/repositories/discover")
    assert res2.status_code == 401

    # List jobs requires auth
    res3 = await client.get("/api/jobs")
    assert res3.status_code == 401


@pytest.mark.asyncio
async def test_discover_with_pat_fallback_when_enabled(authz_env, monkeypatch):
    client, _ = authz_env
    monkeypatch.setattr(settings, "ENABLE_PAT_FALLBACK", True)
    monkeypatch.setattr(settings, "GITHUB_PAT", "ghp_dev_pat_test")

    mock_client_instance = AsyncMock()
    mock_client_instance.get.return_value = httpx.Response(200, json=[])
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance.__aexit__.return_value = None

    with patch("backend.api.repositories._gh.list_user_repos", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [{"id": 11, "full_name": "org/repo-pat", "owner": "org", "name": "repo-pat"}]
        with patch("backend.api.repositories.httpx.AsyncClient", return_value=mock_client_instance):
            res = await client.get("/api/repositories/discover")

    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["full_name"] == "org/repo-pat"


@pytest.mark.asyncio
async def test_multi_user_repository_isolation_and_403(authz_env):
    client, session_factory = authz_env

    # Setup User A and User B
    async with session_factory() as session:
        user_a = User(id="user-a", github_id=1001, username="usera")
        user_b = User(id="user-b", github_id=1002, username="userb")
        session.add_all([user_a, user_b])
        await session.flush()

        # Connect both users
        conn_a = GitHubConnection(user_id="user-a", access_token_encrypted=encrypt_token("gho_a"))
        conn_b = GitHubConnection(user_id="user-b", access_token_encrypted=encrypt_token("gho_b"))
        session.add_all([conn_a, conn_b])

        # Create repo X tracked by User A
        repo_x = Repository(
            id="repo-x",
            github_id=50001,
            full_name="owner/repo-x",
            owner="owner",
            name="repo-x",
            default_branch="main",
        )
        session.add(repo_x)
        await session.flush()

        # Track repo X for User A only
        ur_a = UserRepository(
            user_id="user-a",
            repository_id="repo-x",
            monitoring_enabled=True,
            auto_repair_enabled=False,
            watched_branches=["main"],
        )
        session.add(ur_a)

        # Job on repo X
        job_x = Job(
            id="job-x1",
            repository_id="repo-x",
            pr_number=42,
            pr_title="PR 42",
            base_sha="0123456789abcdef0123456789abcdef01234567",
            head_sha="abcdef0123456789abcdef0123456789abcdef01",
            status="RECEIVED",
            triggered_by_user_id="user-a",
        )
        session.add(job_x)
        await session.commit()

    token_a = create_session_token("user-a", 1001)
    token_b = create_session_token("user-b", 1002)

    # 1. User A lists repos -> sees repo-x
    client.cookies.set(COOKIE_SESSION_NAME, token_a)
    res_a = await client.get("/api/repositories")
    assert res_a.status_code == 200
    assert len(res_a.json()) == 1
    assert res_a.json()[0]["id"] == "repo-x"
    assert res_a.json()[0]["monitoring_enabled"] is True

    # 2. User B lists repos -> sees 0 repos
    client.cookies.set(COOKIE_SESSION_NAME, token_b)
    res_b = await client.get("/api/repositories")
    assert res_b.status_code == 200
    assert len(res_b.json()) == 0

    # 3. User B tries to access User A's repo -> 403 Forbidden
    res_b_patch = await client.patch(
        "/api/repositories/repo-x/monitoring",
        json={"enabled": False},
    )
    assert res_b_patch.status_code == 403
    assert "Forbidden" in res_b_patch.json()["detail"]

    # 4. User B tries to delete User A's repo -> 403 Forbidden
    res_b_del = await client.delete("/api/repositories/repo-x")
    assert res_b_del.status_code == 403

    # 5. User B tries to inspect User A's job -> 403 Forbidden
    res_b_job = await client.get("/api/jobs/job-x1")
    assert res_b_job.status_code == 403

    # User B lists jobs -> empty list (filtered out)
    res_b_jobs = await client.get("/api/jobs")
    assert res_b_jobs.status_code == 200
    assert len(res_b_jobs.json()) == 0


@pytest.mark.asyncio
async def test_shared_repository_independent_configuration(authz_env):
    client, session_factory = authz_env

    # Both User A and User B track the same GitHub repository
    async with session_factory() as session:
        user_a = User(id="user-a2", github_id=2001, username="usera2")
        user_b = User(id="user-b2", github_id=2002, username="userb2")
        session.add_all([user_a, user_b])
        await session.flush()

        repo = Repository(
            id="repo-shared",
            github_id=60001,
            full_name="org/shared-repo",
            owner="org",
            name="shared-repo",
            default_branch="main",
        )
        session.add(repo)
        await session.flush()

        ur_a = UserRepository(
            user_id="user-a2",
            repository_id="repo-shared",
            monitoring_enabled=True,
            auto_repair_enabled=False,
        )
        ur_b = UserRepository(
            user_id="user-b2",
            repository_id="repo-shared",
            monitoring_enabled=False,
            auto_repair_enabled=True,
        )
        session.add_all([ur_a, ur_b])
        await session.commit()

    token_a = create_session_token("user-a2", 2001)
    token_b = create_session_token("user-b2", 2002)

    # Verify User A has monitoring=True, auto_repair=False
    client.cookies.set(COOKIE_SESSION_NAME, token_a)
    res_a = await client.get("/api/repositories")
    assert res_a.status_code == 200
    repo_a = res_a.json()[0]
    assert repo_a["monitoring_enabled"] is True
    assert repo_a["auto_repair_enabled"] is False

    # Verify User B has monitoring=False, auto_repair=True
    client.cookies.set(COOKIE_SESSION_NAME, token_b)
    res_b = await client.get("/api/repositories")
    assert res_b.status_code == 200
    repo_b = res_b.json()[0]
    assert repo_b["monitoring_enabled"] is False
    assert repo_b["auto_repair_enabled"] is True

    # User A unregisters the repo
    client.cookies.set(COOKIE_SESSION_NAME, token_a)
    res_del_a = await client.delete("/api/repositories/repo-shared")
    assert res_del_a.status_code == 204

    # User A now has 0 repos
    res_a_after = await client.get("/api/repositories")
    assert len(res_a_after.json()) == 0

    # User B still has the repo!
    client.cookies.set(COOKIE_SESSION_NAME, token_b)
    res_b_after = await client.get("/api/repositories")
    assert len(res_b_after.json()) == 1

    # Canonical repository row still exists in DB
    async with session_factory() as session:
        canonical_repo = (await session.execute(select(Repository).where(Repository.id == "repo-shared"))).scalars().first()
        assert canonical_repo is not None
