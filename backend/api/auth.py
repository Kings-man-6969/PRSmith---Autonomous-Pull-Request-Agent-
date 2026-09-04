"""GitHub OAuth 2.0 authentication endpoints for PRSmith dashboard users."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.security import (
    COOKIE_OAUTH_STATE_NAME,
    COOKIE_SESSION_NAME,
    create_session_token,
    generate_oauth_state,
    get_current_user,
    get_current_user_optional,
    revoke_user_sessions,
)
from backend.auth.token_encryption import encrypt_token
from backend.config import settings
from backend.database.models import GitHubConnection, User
from backend.database.sessions import get_db_session
from backend.observability.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


@router.get("/github/login")
async def github_login(request: Request) -> RedirectResponse:
    """Initiate GitHub OAuth 2.0 flow with CSRF state protection in httpOnly cookie."""
    if not settings.GITHUB_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub OAuth client ID is not configured",
        )

    # If FRONTEND_URL is an external tunnel (e.g. ngrok) and login was initiated from localhost,
    # redirect to the canonical FRONTEND_URL so the CSRF cookie is set on the exact domain
    # where GitHub's registered callback will return.
    parsed_frontend = urlparse(settings.FRONTEND_URL)
    frontend_domain = parsed_frontend.netloc.split(":")[0]
    req_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    req_domain = req_host.split(":")[0]

    if frontend_domain and frontend_domain not in ("localhost", "127.0.0.1") and req_domain in ("localhost", "127.0.0.1"):
        logger.info(
            "Redirecting OAuth login from local host to canonical tunnel domain for cookie alignment",
            req_host=req_host,
            frontend_url=settings.FRONTEND_URL,
        )
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL.rstrip('/')}/api/auth/github/login",
            status_code=status.HTTP_302_FOUND,
        )

    state = generate_oauth_state()
    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "scope": settings.GITHUB_OAUTH_SCOPES,
        "state": state,
    }
    github_auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    response = RedirectResponse(url=github_auth_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=COOKIE_OAUTH_STATE_NAME,
        value=state,
        httponly=True,
        samesite="lax",
        max_age=settings.OAUTH_STATE_EXPIRE_SECONDS,
        secure=settings.COOKIE_SECURE,
        path="/",
    )
    return response


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Handle OAuth callback, exchange authorization code, upsert user and session cookie."""
    cookie_state = request.cookies.get(COOKIE_OAUTH_STATE_NAME)

    # 1. CSRF State Verification
    if not state or not cookie_state or state != cookie_state:
        logger.warning("OAuth state mismatch or missing CSRF cookie")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state (CSRF verification failed)",
        )

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authorization code was not provided by GitHub",
        )

    # 2. Exchange authorization code for access token
    token_url = "https://github.com/login/oauth/access_token"
    token_payload = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
        "code": code,
        "state": state,
    }
    headers = {"Accept": "application/json"}

    async with httpx.AsyncClient() as client:
        token_res = await client.post(token_url, data=token_payload, headers=headers)
        if token_res.status_code != 200:
            logger.error("Failed token exchange with GitHub", status=token_res.status_code, body=token_res.text)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to exchange authorization code with GitHub",
            )

        token_data = token_res.json()
        if "error" in token_data:
            logger.error("GitHub returned OAuth error", error=token_data.get("error"), desc=token_data.get("error_description"))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GitHub OAuth error: {token_data.get('error_description', token_data.get('error'))}",
            )

        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="GitHub did not return an access token",
            )

        # 3. Retrieve user profile and verified email from GitHub
        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github.v3+json",
        }
        user_res = await client.get("https://api.github.com/user", headers=auth_headers)
        if user_res.status_code != 200:
            logger.error("Failed fetching GitHub user profile", status=user_res.status_code)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to retrieve user profile from GitHub",
            )

        user_info = user_res.json()
        github_id = user_info["id"]
        username = user_info["login"]
        avatar_url = user_info.get("avatar_url")
        email = user_info.get("email")

        # Try to resolve verified primary email if not public in profile
        if not email:
            emails_res = await client.get("https://api.github.com/user/emails", headers=auth_headers)
            if emails_res.status_code == 200:
                emails_list = emails_res.json()
                for em in emails_list:
                    if em.get("primary") and em.get("verified"):
                        email = em.get("email")
                        break
                if not email and emails_list:
                    email = emails_list[0].get("email")

    # 4. Upsert User in database
    user_stmt = select(User).where(User.github_id == github_id)
    user_result = await session.execute(user_stmt)
    user = user_result.scalars().first()

    now = datetime.now(timezone.utc)
    if not user:
        user = User(
            github_id=github_id,
            username=username,
            email=email,
            avatar_url=avatar_url,
            role="admin",  # initial dashboard user is admin
        )
        session.add(user)
        await session.flush()
    else:
        user.username = username
        if email:
            user.email = email
        if avatar_url:
            user.avatar_url = avatar_url
        user.updated_at = now

    # 5. Upsert GitHubConnection with encrypted tokens
    conn_stmt = select(GitHubConnection).where(GitHubConnection.user_id == user.id)
    conn_result = await session.execute(conn_stmt)
    connection = conn_result.scalars().first()

    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    refresh_token_expires_in = token_data.get("refresh_token_expires_in")
    scopes = token_data.get("scope", settings.GITHUB_OAUTH_SCOPES)
    token_type = token_data.get("token_type", "bearer")

    encrypted_access = encrypt_token(access_token)
    encrypted_refresh = encrypt_token(refresh_token) if refresh_token else None

    access_expires_at = now + timedelta(seconds=int(expires_in)) if expires_in else None
    refresh_expires_at = (
        now + timedelta(seconds=int(refresh_token_expires_in)) if refresh_token_expires_in else None
    )

    if not connection:
        connection = GitHubConnection(
            user_id=user.id,
            access_token_encrypted=encrypted_access,
            refresh_token_encrypted=encrypted_refresh,
            access_token_expires_at=access_expires_at,
            refresh_token_expires_at=refresh_expires_at,
            scopes=scopes,
            token_type=token_type,
        )
        session.add(connection)
    else:
        connection.access_token_encrypted = encrypted_access
        if encrypted_refresh:
            connection.refresh_token_encrypted = encrypted_refresh
        connection.access_token_expires_at = access_expires_at
        connection.refresh_token_expires_at = refresh_expires_at
        connection.scopes = scopes
        connection.token_type = token_type
        connection.updated_at = now

    await session.commit()

    # Issue PRSmith session token and set httpOnly cookie
    session_jwt = create_session_token(user.id, user.github_id, session_version=user.session_version)
    raw_proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    forwarded_proto = raw_proto.split(",")[0].strip() if raw_proto else "http"
    raw_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    forwarded_host = raw_host.split(",")[0].strip()
    if forwarded_host and ":8000" not in forwarded_host:
        target_redirect = f"{forwarded_proto}://{forwarded_host}/repos"
    else:
        target_redirect = f"{settings.FRONTEND_URL}/repos"
    response = RedirectResponse(url=target_redirect, status_code=status.HTTP_302_FOUND)

    # Clean up short-lived CSRF state cookie
    response.delete_cookie(key=COOKIE_OAUTH_STATE_NAME, path="/")

    # Set persistent session cookie
    response.set_cookie(
        key=COOKIE_SESSION_NAME,
        value=session_jwt,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        secure=settings.COOKIE_SECURE,
    )
    logger.info("Successfully completed GitHub OAuth login", user_id=user.id, username=user.username)
    return response


@router.get("/me")
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve profile and identity details of the authenticated dashboard user."""
    return {
        "id": current_user.id,
        "github_id": current_user.github_id,
        "username": current_user.username,
        "email": current_user.email,
        "avatar_url": current_user.avatar_url,
        "role": current_user.role,
        "session_version": current_user.session_version,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
    }


@router.post("/logout")
async def logout_user(
    current_user: Optional[User] = Depends(get_current_user_optional),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Clear session cookie and increment session_version to invalidate JWTs."""
    if current_user:
        await revoke_user_sessions(session, current_user)
        logger.info("User session revoked on logout", user_id=current_user.id, session_version=current_user.session_version)

    response = JSONResponse(content={"status": "logged_out", "message": "Session invalidated and cookie cleared"})
    response.delete_cookie(
        key=COOKIE_SESSION_NAME,
        path="/",
    )
    return response
