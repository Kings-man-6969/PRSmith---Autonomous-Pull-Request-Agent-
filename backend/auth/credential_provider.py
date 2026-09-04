"""GitHub Credential Provider abstracting App tokens, OAuth user tokens, and dev PATs."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.github_app import GitHubAppAuth
from backend.auth.token_encryption import decrypt_token, encrypt_token
from backend.config import settings
from backend.database.models import GitHubConnection
from backend.observability.logging import get_logger

logger = get_logger(__name__)


class AuthError(Exception):
    """Base exception for all authentication and credential resolution errors."""
    pass


class AuthRequired(AuthError):
    """Raised when authentication credentials or user sessions are missing."""
    pass


class AuthRevoked(AuthError):
    """Raised when credentials have been revoked, invalidated, or permanently expired."""
    pass


class AuthInsufficientScope(AuthError):
    """Raised when an authenticated credential lacks required OAuth or App permissions."""
    pass


class AuthTemporaryFailure(AuthError):
    """Raised on transient network, timeout, or upstream provider 5xx errors during auth."""
    pass


class CredentialExpiredError(AuthRevoked):
    """Deprecated alias for AuthRevoked for backwards compatibility."""
    pass


class GitHubCredentialProvider:
    """Centralized credential resolution provider with explicit precedence:

    1. GitHub App installation token (for automated PR review/repair operations)
    2. User OAuth access token (for user-authorized repo discovery/registration and user-triggered jobs)
    3. Development PAT (strictly for local dev fallback when ENABLE_PAT_FALLBACK=True)
    """

    def __init__(self, app_auth: Optional[GitHubAppAuth] = None):
        self._app_auth = app_auth or GitHubAppAuth()

    async def get_app_credential(self, installation_id: int) -> str:
        """Fetch an installation token using GitHub App RS256 JWT flow."""
        if not installation_id:
            raise AuthRequired("No installation_id provided for GitHub App credential")
        try:
            return await self._app_auth.get_installation_token(installation_id)
        except Exception as exc:
            msg = str(exc).lower()
            if "not found" in msg or "suspended" in msg or "revoked" in msg:
                raise AuthRevoked(f"GitHub App installation {installation_id} revoked or suspended: {exc}") from exc
            raise AuthTemporaryFailure(f"Temporary failure obtaining GitHub App token: {exc}") from exc

    def assert_has_scopes(self, granted_scopes: str, required_scopes: list[str]) -> None:
        """Verify that granted OAuth scopes include all required scopes."""
        granted_set = set(filter(None, [s.strip() for s in (granted_scopes or "").replace(",", " ").split()]))
        missing = [req for req in required_scopes if req not in granted_set]
        if missing:
            raise AuthInsufficientScope(f"Missing required GitHub OAuth scopes: {', '.join(missing)}")

    async def get_user_credential(
        self,
        user_id: str,
        session: AsyncSession,
        required_scopes: Optional[list[str]] = None,
    ) -> str:
        """Return a valid GitHub OAuth access token for the user.

        Token lifecycle:
        1. Load GitHubConnection for user_id with SELECT FOR UPDATE (on PostgreSQL)
           to ensure concurrency safety during token refresh.
        2. If access_token_expires_at is None -> classic OAuth App (no expiry), decrypt and return.
        3. If access_token_expires_at is set and still valid (with 5-minute buffer) -> decrypt and return.
        4. If token is expired and refresh_token_encrypted is available:
           a. POST to GitHub token refresh endpoint.
           b. Update github_connections with new encrypted tokens and expiry timestamps.
           c. Commit session and return new decrypted access token.
        5. If token is expired and no refresh_token is available -> raise AuthRevoked.
        """
        if not user_id:
            raise AuthRequired("No user_id provided for credential resolution")

        stmt = select(GitHubConnection).where(GitHubConnection.user_id == user_id)
        # Apply row-level lock on engines that support SELECT FOR UPDATE
        bind = session.bind
        dialect_name = bind.dialect.name if bind else ""
        if dialect_name != "sqlite":
            stmt = stmt.with_for_update()

        result = await session.execute(stmt)
        conn = result.scalars().first()

        if not conn:
            raise AuthRequired(f"No GitHub connection found for user {user_id}")

        if required_scopes:
            self.assert_has_scopes(conn.scopes, required_scopes)

        now = datetime.now(timezone.utc)

        # 2. Classic OAuth token without expiration
        if conn.access_token_expires_at is None:
            return decrypt_token(conn.access_token_encrypted)

        # 3. Token is still valid (using 5-minute safety buffer)
        expires_at = conn.access_token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if now < (expires_at - timedelta(minutes=5)):
            return decrypt_token(conn.access_token_encrypted)

        # 4. Token is expired — check for refresh token
        if not conn.refresh_token_encrypted:
            raise CredentialExpiredError(
                f"OAuth token expired for user {user_id} and no refresh token available"
            )

        plain_refresh_token = decrypt_token(conn.refresh_token_encrypted)
        if not plain_refresh_token:
            raise CredentialExpiredError(
                f"Refresh token could not be decrypted for user {user_id}"
            )

        # Execute refresh with GitHub OAuth endpoint
        refresh_url = "https://github.com/login/oauth/access_token"
        payload = {
            "client_id": settings.GITHUB_CLIENT_ID,
            "client_secret": settings.GITHUB_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": plain_refresh_token,
        }
        headers = {"Accept": "application/json"}

        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(refresh_url, data=payload, headers=headers, timeout=15.0)
        except httpx.RequestError as exc:
            logger.error("Failed network request refreshing GitHub OAuth token", error=str(exc))
            raise AuthTemporaryFailure(f"Network error refreshing GitHub token: {exc}") from exc
        except Exception as exc:
            logger.error("Unexpected error refreshing GitHub OAuth token", error=str(exc))
            raise AuthTemporaryFailure(f"Unexpected error refreshing GitHub token: {exc}") from exc

        if res.status_code != 200:
            logger.error("GitHub OAuth refresh rejected", status=res.status_code, body=res.text)
            if res.status_code in (400, 401, 403):
                raise AuthRevoked(f"GitHub OAuth refresh rejected ({res.status_code}): {res.text}")
            raise AuthTemporaryFailure(f"GitHub OAuth refresh temporary error ({res.status_code}): {res.text}")

        data = res.json()
        if "error" in data:
            logger.error("GitHub OAuth refresh error", error=data.get("error"), description=data.get("error_description"))
            error_code = data.get("error", "")
            error_desc = data.get("error_description", error_code)
            if error_code in ("bad_verification_code", "invalid_grant", "unauthorized_client"):
                raise AuthRevoked(f"GitHub OAuth token permanently revoked or expired: {error_desc}")
            raise AuthRevoked(f"GitHub OAuth error: {error_desc}")

        new_access_token = data.get("access_token")
        if not new_access_token:
            raise AuthTemporaryFailure("GitHub OAuth refresh did not return access_token")

        new_refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in")
        refresh_token_expires_in = data.get("refresh_token_expires_in")

        conn.access_token_encrypted = encrypt_token(new_access_token)
        if new_refresh_token:
            conn.refresh_token_encrypted = encrypt_token(new_refresh_token)

        if expires_in:
            conn.access_token_expires_at = now + timedelta(seconds=int(expires_in))
        if refresh_token_expires_in:
            conn.refresh_token_expires_at = now + timedelta(seconds=int(refresh_token_expires_in))

        conn.updated_at = now
        await session.commit()

        logger.info("Successfully refreshed GitHub OAuth token for user", user_id=user_id)
        return new_access_token

    def get_dev_pat(self) -> Optional[str]:
        """Return GITHUB_PAT only if ENABLE_PAT_FALLBACK is explicitly enabled."""
        if not settings.ENABLE_PAT_FALLBACK:
            return None
        return settings.GITHUB_PAT.strip() if settings.GITHUB_PAT else None
