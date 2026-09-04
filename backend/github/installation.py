"""GitHub App Installation service and lifecycle management."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.credential_provider import AuthRevoked
from backend.database.models import GitHubInstallation, Repository
from backend.observability.logging import get_logger

logger = get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InstallationService:
    """Service to manage GitHub App installations, permissions, and repository bindings."""

    @staticmethod
    async def get_installation_by_github_id(
        session: AsyncSession, installation_id: int
    ) -> Optional[GitHubInstallation]:
        """Fetch a GitHubInstallation by its GitHub installation ID."""
        stmt = select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def upsert_installation(
        session: AsyncSession,
        installation_id: int,
        account_login: str,
        account_type: str = "Organization",
        app_slug: Optional[str] = None,
        permissions: Optional[Dict[str, Any]] = None,
        events: Optional[List[str]] = None,
        status: str = "active",
    ) -> GitHubInstallation:
        """Upsert a GitHub App installation record."""
        inst = await InstallationService.get_installation_by_github_id(session, installation_id)
        now = utcnow()
        if not inst:
            inst = GitHubInstallation(
                installation_id=installation_id,
                account_login=account_login,
                account_type=account_type,
                app_slug=app_slug,
                permissions=permissions or {},
                events=events or [],
                status=status,
                created_at=now,
                updated_at=now,
            )
            session.add(inst)
        else:
            inst.account_login = account_login
            inst.account_type = account_type
            if app_slug:
                inst.app_slug = app_slug
            if permissions is not None:
                inst.permissions = permissions
            if events is not None:
                inst.events = events
            inst.status = status
            if status == "suspended":
                inst.suspended_at = now
            elif status == "active":
                inst.suspended_at = None
            inst.updated_at = now

        await session.flush()
        return inst

    @staticmethod
    async def link_repository(
        session: AsyncSession,
        repository: Repository,
        installation_id: int,
    ) -> Repository:
        """Ensure installation exists and link repository to it with both integer ID and FK."""
        inst = await InstallationService.get_installation_by_github_id(session, installation_id)
        if not inst:
            inst = await InstallationService.upsert_installation(
                session=session,
                installation_id=installation_id,
                account_login=repository.owner,
            )

        repository.installation_id = installation_id
        repository.github_installation_id = inst.id
        await session.flush()
        return repository

    @staticmethod
    async def assert_installation_active(
        session: AsyncSession, installation_id: int
    ) -> GitHubInstallation:
        """Verify that the given GitHub installation is registered and active.

        Raises:
            AuthRevoked: If the installation cannot be found, is deleted, or is suspended.
        """
        inst = await InstallationService.get_installation_by_github_id(session, installation_id)
        if not inst:
            raise AuthRevoked(f"GitHub App installation {installation_id} not registered")
        if inst.status == "suspended":
            raise AuthRevoked(
                f"GitHub App installation {installation_id} is suspended (suspended_at={inst.suspended_at})"
            )
        if inst.status == "deleted":
            raise AuthRevoked(f"GitHub App installation {installation_id} has been uninstalled/deleted")
        return inst

    @staticmethod
    async def handle_webhook_event(
        session: AsyncSession,
        action: str,
        payload: Dict[str, Any],
    ) -> Optional[GitHubInstallation]:
        """Handle GitHub App installation webhook lifecycle events."""
        installation_data = payload.get("installation", {})
        inst_id = installation_data.get("id")
        if not inst_id:
            logger.warning("Installation webhook event missing installation ID", action=action)
            return None

        account = installation_data.get("account", {})
        account_login = account.get("login", "")
        account_type = account.get("type", "Organization")
        permissions = installation_data.get("permissions", {})
        events = installation_data.get("events", [])
        app_slug = installation_data.get("app_slug")

        if action in ("created", "unsuspend"):
            inst = await InstallationService.upsert_installation(
                session=session,
                installation_id=inst_id,
                account_login=account_login,
                account_type=account_type,
                app_slug=app_slug,
                permissions=permissions,
                events=events,
                status="active",
            )
            logger.info("GitHub installation activated/updated", installation_id=inst_id, action=action)
            return inst

        elif action == "suspend":
            inst = await InstallationService.upsert_installation(
                session=session,
                installation_id=inst_id,
                account_login=account_login,
                account_type=account_type,
                app_slug=app_slug,
                permissions=permissions,
                events=events,
                status="suspended",
            )
            logger.warning("GitHub installation suspended", installation_id=inst_id)
            return inst

        elif action == "deleted":
            inst = await InstallationService.get_installation_by_github_id(session, inst_id)
            if inst:
                inst.status = "deleted"
                inst.updated_at = utcnow()
                await session.flush()
                logger.info("GitHub installation marked deleted", installation_id=inst_id)
            return inst

        return None
