import hashlib
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession, User
from app.db.session import get_session


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_session_response(
    session: AsyncSession,
    user: User,
    request: Request,
    response: Response,
) -> None:
    settings = get_settings()
    now = utc_now()
    token = secrets.token_urlsafe(32)
    row = AuthSession(
        user_id=user.id,
        session_token_hash=_hash_token(token),
        expires_at=now + timedelta(hours=settings.auth_session_ttl_hours),
        created_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", ""),
    )
    user.last_login_at = now
    session.add(row)
    await session.commit()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.auth_session_ttl_hours * 3600,
        path="/",
    )


async def create_login_session(
    db: AsyncSession,
    user: User,
    request: Request,
    response: Response,
) -> None:
    await create_session_response(db, user, request, response)


async def get_current_user_and_session(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> tuple[User, AuthSession]:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        request.state.access_auth_failure = "unauthenticated"
        raise HTTPException(status_code=401, detail="Authentication required")
    result = await session.execute(
        select(AuthSession, User)
        .join(User, AuthSession.user_id == User.id)
        .where(AuthSession.session_token_hash == _hash_token(token))
        .where(AuthSession.revoked_at.is_(None))
        .where(AuthSession.expires_at >= utc_now())
    )
    row = result.first()
    if row is None:
        request.state.access_auth_failure = "unauthenticated"
        raise HTTPException(status_code=401, detail="Authentication required")
    auth_session, user = row
    if user.is_banned:
        request.state.access_auth_failure = "forbidden"
        raise HTTPException(status_code=403, detail="Account is unavailable")
    request.state.v2_actor_id = user.id
    request.state.access_auth_failure = None
    return user, auth_session


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    user, _auth_session = await get_current_user_and_session(request, session)
    return user


async def revoke_current_session(request: Request, response: Response, session: AsyncSession) -> None:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        result = await session.execute(
            select(AuthSession).where(AuthSession.session_token_hash == _hash_token(token))
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.revoked_at = utc_now()
            await session.commit()
    # Do not emit a cookie deletion: this response may arrive after another tab
    # has logged in and replaced the cookie. Revoking the presented token above
    # ends this session immediately; its inert cookie expires or is replaced at login.
