"""Unit tests for structured authentication taxonomy and GitHub installation management."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.credential_provider import (
    AuthInsufficientScope,
    AuthRequired,
    AuthRevoked,
    AuthTemporaryFailure,
    GitHubCredentialProvider,
)
from backend.database.models import GitHubInstallation, Repository
from backend.github.installation import InstallationService


def test_auth_exception_hierarchy():
    """Verify exception hierarchy conforms to taxonomy requirements."""
    assert issubclass(AuthRequired, Exception)
    assert issubclass(AuthRevoked, Exception)
    assert issubclass(AuthInsufficientScope, Exception)
    assert issubclass(AuthTemporaryFailure, Exception)


def test_scope_validation():
    """assert_has_scopes correctly verifies required OAuth scopes."""
    provider = GitHubCredentialProvider()

    # Sufficient scopes
    provider.assert_has_scopes("repo read:org read:user", ["repo", "read:org"])

    # Missing scopes
    with pytest.raises(AuthInsufficientScope) as exc:
        provider.assert_has_scopes("read:user", ["repo", "workflow"])
    assert "repo" in str(exc.value)


@pytest.mark.asyncio
async def test_installation_service_lifecycle(db_session: AsyncSession):
    """Test upsert, linking, and status assertion for GitHub App installations."""
    # 1. Upsert installation
    inst = await InstallationService.upsert_installation(
        session=db_session,
        installation_id=999888,
        account_login="acme-corp",
        account_type="Organization",
        permissions={"pull_requests": "write"},
    )
    await db_session.commit()
    assert inst.installation_id == 999888
    assert inst.status == "active"

    # 2. Link repository
    repo = Repository(
        github_id=55555,
        full_name="acme-corp/super-repo",
        owner="acme-corp",
        name="super-repo",
    )
    db_session.add(repo)
    await db_session.flush()

    linked_repo = await InstallationService.link_repository(
        session=db_session,
        repository=repo,
        installation_id=999888,
    )
    await db_session.commit()

    assert linked_repo.installation_id == 999888
    assert linked_repo.github_installation_id == inst.id

    # 3. Assert active succeeds
    active_inst = await InstallationService.assert_installation_active(db_session, 999888)
    assert active_inst.id == inst.id

    # 4. Suspend installation -> assert_active raises AuthRevoked
    await InstallationService.handle_webhook_event(
        session=db_session,
        action="suspend",
        payload={"installation": {"id": 999888, "account": {"login": "acme-corp"}}},
    )
    await db_session.commit()

    with pytest.raises(AuthRevoked) as exc:
        await InstallationService.assert_installation_active(db_session, 999888)
    assert "suspended" in str(exc.value).lower()
