from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthLoginFailure


def _raise_rate_limited(retry_after_seconds: int) -> None:
    raise HTTPException(
        status_code=429,
        detail="login_rate_limited",
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )


async def enforce_login_failure_limit(session: AsyncSession, email: str, client_ip: str) -> None:
    settings = get_settings()
    now = utc_now()
    cutoff = now - timedelta(seconds=settings.auth_login_failure_window_seconds)
    normalized_email = email.lower().strip()

    await session.execute(delete(AuthLoginFailure).where(AuthLoginFailure.created_at < cutoff))

    email_count = await session.scalar(
        select(func.count())
        .select_from(AuthLoginFailure)
        .where(AuthLoginFailure.email == normalized_email)
        .where(AuthLoginFailure.created_at >= cutoff)
    )
    if (email_count or 0) >= settings.auth_login_failure_max_per_email:
        retry_after = await _retry_after_from_window(
            session,
            cutoff,
            now,
            AuthLoginFailure.email == normalized_email,
        )
        _raise_rate_limited(retry_after)

    ip_count = await session.scalar(
        select(func.count())
        .select_from(AuthLoginFailure)
        .where(AuthLoginFailure.client_ip == client_ip)
        .where(AuthLoginFailure.created_at >= cutoff)
    )
    if (ip_count or 0) >= settings.auth_login_failure_max_per_ip:
        retry_after = await _retry_after_from_window(
            session,
            cutoff,
            now,
            AuthLoginFailure.client_ip == client_ip,
        )
        _raise_rate_limited(retry_after)


async def record_login_failure(session: AsyncSession, email: str, client_ip: str) -> None:
    session.add(AuthLoginFailure(email=email.lower().strip(), client_ip=client_ip))
    await session.commit()


async def clear_login_failures(session: AsyncSession, email: str, client_ip: str) -> None:
    del client_ip
    normalized_email = email.lower().strip()
    await session.execute(
        delete(AuthLoginFailure).where(AuthLoginFailure.email == normalized_email)
    )
    await session.commit()


async def _retry_after_from_window(session: AsyncSession, cutoff, now, *where_clauses) -> int:
    settings = get_settings()
    oldest_created_at = await session.scalar(
        select(AuthLoginFailure.created_at)
        .where(*where_clauses)
        .where(AuthLoginFailure.created_at >= cutoff)
        .order_by(AuthLoginFailure.created_at.asc())
        .limit(1)
    )
    if oldest_created_at is None:
        return settings.auth_login_failure_window_seconds
    window_end = oldest_created_at + timedelta(seconds=settings.auth_login_failure_window_seconds)
    return max(1, int((window_end - now).total_seconds()))
