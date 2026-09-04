"""GitHub App authentication using RS256 JWTs and installation access tokens."""

import time
from pathlib import Path
from typing import Dict, Optional
import jwt
import httpx

from backend.config import settings
from backend.observability.logging import get_logger

logger = get_logger(__name__)


class GitHubAppAuth:
    """Manages GitHub App JWTs and installation token issuance."""

    def __init__(
        self,
        app_id: Optional[str] = None,
        private_key_path: Optional[str] = None,
    ):
        self.app_id = app_id or settings.GITHUB_APP_ID
        self.key_path = private_key_path or settings.GITHUB_APP_PRIVATE_KEY_PATH
        self._private_key: Optional[str] = None
        self._token_cache: Dict[int, tuple[str, float]] = {}  # inst_id -> (token, expire_time)

    def _get_private_key(self) -> str:
        if self._private_key:
            return self._private_key
        if settings.GITHUB_APP_PRIVATE_KEY:
            self._private_key = settings.GITHUB_APP_PRIVATE_KEY
            return self._private_key

        key_file = Path(self.key_path)
        if key_file.exists():
            with open(key_file, "r", encoding="utf-8") as f:
                self._private_key = f.read()
            return self._private_key
        return ""

    def generate_jwt(self) -> str:
        """Create a RS256 signed JWT valid for 10 minutes."""
        pk = self._get_private_key()
        if not pk or not self.app_id:
            return ""

        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + (10 * 60),
            "iss": self.app_id,
        }
        return jwt.encode(payload, pk, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        """Get or refresh installation token."""
        now = time.time()
        if installation_id in self._token_cache:
            token, exp = self._token_cache[installation_id]
            if now < exp - 300:  # 5 min buffer
                return token

        app_jwt = self.generate_jwt()
        if not app_jwt:
            return ""

        url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers)
            if res.status_code == 201:
                data = res.json()
                token = data["token"]
                self._token_cache[installation_id] = (token, now + 3600)
                return token
            logger.error("Failed to obtain installation token", status=res.status_code, body=res.text)
            return ""
