"""OAuth session security, JWT token management, and authentication dependencies."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database.models import User
from backend.database.sessions import get_db_session

COOKIE_SESSION_NAME = "prsmith_session"
COOKIE_OAUTH_STATE_NAME = "oauth_state"


def generate_oauth_state() -> str:
    """Generate a high-entropy random state string for OAuth CSRF protection."""
    return secrets.token_urlsafe(32)


def create_session_token(user_id: str, github_id: int, session_version: int = 1) -> str:
    """Create a signed HS256 JWT session token for an authenticated user."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "github_id": github_id,
        "session_version": session_version,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_session_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a session JWT.

    Returns the payload dictionary if valid, or None if expired/invalid.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except (jwt.PyJWTError, Exception):
        return None


def _extract_token_from_request(request: Request) -> Optional[str]:
    """Extract session token from httpOnly cookie or Authorization: Bearer header."""
    cookies = getattr(request, "cookies", None)
    if isinstance(cookies, dict):
        cookie_token = cookies.get(COOKIE_SESSION_NAME)
        if isinstance(cookie_token, str) and cookie_token:
            return cookie_token

    headers = getattr(request, "headers", None)
    if headers and hasattr(headers, "get"):
        auth_header = headers.get("Authorization") or headers.get("authorization")
        if isinstance(auth_header, str) and auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()
    return None


async def revoke_user_sessions(session: AsyncSession, user: User) -> int:
    """Increment session_version to invalidate all existing active JWT tokens for this user."""
    user.session_version = (user.session_version or 1) + 1
    user.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return user.session_version


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """FastAPI dependency to extract and authenticate the current user from cookie or bearer header.

    Validates signature, expiration, and session_version against current DB state.
    Raises HTTP 401 if unauthenticated, expired, user not found, or session revoked.
    """
    token = _extract_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    payload = decode_session_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    user_id = payload["sub"]
    token_session_version = payload.get("session_version")

    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    user_session_version = user.session_version if user.session_version is not None else 1
    if token_session_version is None or token_session_version != user_session_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked or invalidated",
        )

    return user


async def get_current_user_optional(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Optional[User]:
    """FastAPI dependency returning the current User or None if unauthenticated or session invalid.

    Only used on endpoints that explicitly permit unauthenticated or fallback workflows.
    """
    token = _extract_token_from_request(request)
    if not token:
        return None

    payload = decode_session_token(token)
    if not payload or "sub" not in payload:
        return None

    user_id = payload["sub"]
    token_session_version = payload.get("session_version")

    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalars().first()
    if not user:
        return None

    if token_session_version is None or token_session_version != user.session_version:
        return None

    return user
