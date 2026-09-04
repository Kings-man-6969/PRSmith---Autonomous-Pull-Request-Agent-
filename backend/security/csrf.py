"""Double-submit cookie CSRF protection for PRSmith API endpoints."""

import secrets
from typing import Optional
from fastapi import HTTPException, Request, Response, status

from backend.config import settings

CSRF_COOKIE_NAME = "prsmith_csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
MIN_CSRF_TOKEN_LENGTH = 16


def generate_csrf_token() -> str:
    """Generate a high-entropy cryptographically secure CSRF token."""
    return secrets.token_urlsafe(32)


def set_csrf_cookie(
    response: Response,
    token: Optional[str] = None,
    max_age: int = 86400,
) -> str:
    """Set double-submit CSRF token cookie.

    Crucially, HttpOnly is False so legitimate frontend JavaScript can read the
    token from document.cookie and submit it in the X-CSRF-Token request header.
    """
    token_val = token or generate_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token_val,
        httponly=False,  # Must be readable by client JS
        samesite="lax",
        secure=settings.COOKIE_SECURE,
        path="/",
        max_age=max_age,
    )
    return token_val


def extract_csrf_tokens(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Extract CSRF token from request cookie and header."""
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME) or request.cookies.get("csrf_token")
    header_token = (
        request.headers.get("x-csrf-token")
        or request.headers.get("X-CSRF-Token")
        or request.headers.get("X-CSRF-TOKEN")
    )
    return cookie_token, header_token


async def verify_csrf(request: Request) -> str:
    """FastAPI dependency to verify double-submit CSRF protection.

    Enforces that:
    1. CSRF cookie is present and non-empty.
    2. X-CSRF-Token header is present and non-empty.
    3. Cookie token and header token match exactly using constant-time comparison.
    4. Token meets minimum entropy length requirements.

    Raises:
        HTTPException(403): If either cookie or header is missing, or if tokens mismatch.
    """
    cookie_token, header_token = extract_csrf_tokens(request)

    if not cookie_token and not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed: Missing both CSRF cookie and X-CSRF-Token header",
        )

    if not cookie_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed: Missing CSRF cookie",
        )

    if not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed: Missing X-CSRF-Token header",
        )

    if len(cookie_token) < MIN_CSRF_TOKEN_LENGTH or len(header_token) < MIN_CSRF_TOKEN_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed: Invalid token format",
        )

    if not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF verification failed: Token mismatch between cookie and header",
        )

    return header_token
