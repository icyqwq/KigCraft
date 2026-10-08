import logging
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.redis_client import get_redis_client
from app.core.time import utc_now
from app.db.models import EmailVerificationCode

logger = logging.getLogger(__name__)


def _raise_rate_limited(retry_after_seconds: int) -> None:
    raise HTTPException(
        status_code=429,
        detail="verification_code_rate_limited",
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )


async def enforce_verification_code_send_limits(
    session: AsyncSession,
    email: str,
    client_ip: str,
) -> None:
    settings = get_settings()
    retry_after = await _enforce_redis_send_limits(email, client_ip, settings)
    if retry_after is not None:
        _raise_rate_limited(retry_after)
    await _enforce_db_send_limits(session, email, client_ip, settings)


async def _enforce_redis_send_limits(email: str, client_ip: str, settings: Settings) -> int | None:
    try:
        redis = get_redis_client()
        cooldown_key = f"auth:send-code:cooldown:{email}"
        cooldown_ttl = await redis.ttl(cooldown_key)
        if settings.email_code_send_cooldown_seconds > 0 and cooldown_ttl > 0:
            return cooldown_ttl

        email_key = f"auth:send-code:email:{email}"
        ip_key = f"auth:send-code:ip:{client_ip}"
        window_seconds = settings.email_code_send_window_seconds

        email_count = await redis.incr(email_key)
        if email_count == 1:
            await redis.expire(email_key, window_seconds)
        if email_count > settings.email_code_send_max_per_email:
            ttl = await redis.ttl(email_key)
            return max(1, ttl if ttl > 0 else window_seconds)

        ip_count = await redis.incr(ip_key)
        if ip_count == 1:
            await redis.expire(ip_key, window_seconds)
        if ip_count > settings.email_code_send_max_per_ip:
            ttl = await redis.ttl(ip_key)
            return max(1, ttl if ttl > 0 else window_seconds)

        if settings.email_code_send_cooldown_seconds > 0:
            await redis.set(
                cooldown_key,
                "1",
                ex=settings.email_code_send_cooldown_seconds,
            )
        return None
    except Exception as exc:
        logger.warning("Redis verification send rate limit unavailable: %s", exc)
        return None


async def _enforce_db_send_limits(
    session: AsyncSession,
    email: str,
    client_ip: str,
    settings: Settings,
) -> None:
    now = utc_now()
    cutoff = now - timedelta(seconds=settings.email_code_send_window_seconds)

    last_sent = await session.scalar(
        select(func.max(EmailVerificationCode.created_at)).where(EmailVerificationCode.email == email)
    )
    if last_sent is not None and settings.email_code_send_cooldown_seconds > 0:
        elapsed_seconds = (now - last_sent).total_seconds()
        if elapsed_seconds < settings.email_code_send_cooldown_seconds:
            _raise_rate_limited(int(settings.email_code_send_cooldown_seconds - elapsed_seconds))

    email_count = await session.scalar(
        select(func.count())
        .select_from(EmailVerificationCode)
        .where(EmailVerificationCode.email == email)
        .where(EmailVerificationCode.created_at >= cutoff)
    )
    if (email_count or 0) >= settings.email_code_send_max_per_email:
        retry_after = await _retry_after_from_window(
            session,
            cutoff,
            now,
            EmailVerificationCode.email == email,
        )
        _raise_rate_limited(retry_after)

    ip_count = await session.scalar(
        select(func.count())
        .select_from(EmailVerificationCode)
        .where(EmailVerificationCode.send_ip == client_ip)
        .where(EmailVerificationCode.created_at >= cutoff)
    )
    if (ip_count or 0) >= settings.email_code_send_max_per_ip:
        retry_after = await _retry_after_from_window(
            session,
            cutoff,
            now,
            EmailVerificationCode.send_ip == client_ip,
        )
        _raise_rate_limited(retry_after)


async def _retry_after_from_window(session: AsyncSession, cutoff, now, *where_clauses) -> int:
    settings = get_settings()
    oldest_created_at = await session.scalar(
        select(EmailVerificationCode.created_at)
        .where(*where_clauses)
        .where(EmailVerificationCode.created_at >= cutoff)
        .order_by(EmailVerificationCode.created_at.asc())
        .limit(1)
    )
    if oldest_created_at is None:
        return settings.email_code_send_window_seconds
    window_end = oldest_created_at + timedelta(seconds=settings.email_code_send_window_seconds)
    return max(1, int((window_end - now).total_seconds()))
