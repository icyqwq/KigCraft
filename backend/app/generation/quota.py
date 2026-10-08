from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.quota.settings import QuotaPolicy, get_quota_policy
from app.auth.models import UserRole
from app.core.time import utc_now
from app.db.models import User, UserGenerationQuotaWindow

from app.design.service import ApplicationError
from app.quota.cutover import account_lock, wallet_if_installed, legacy_refund

logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class GenerationQuotaState(BaseModel):
    limit: int
    used: int
    remaining: int
    reset_at: datetime
    window_start_at: datetime
    exempt: bool = False


class GenerationQuotaResetResult(BaseModel):
    reset_count: int
    window_start_at: datetime


class GenerationQuotaBalanceAdjustResult(BaseModel):
    balance: int


class GenerationDailyQuotaExceeded(HTTPException):
    def __init__(self, state: GenerationQuotaState) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "generation_daily_quota_exceeded",
                "limit": state.limit,
                "used": state.used,
                "remaining": state.remaining,
                "reset_at": state.reset_at.isoformat(),
            },
        )


def _aware_utc(value: datetime | None = None) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def current_generation_quota_window(
    now: datetime | None = None,
    reset_hour: int | None = None,
) -> tuple[datetime, datetime]:
    policy = get_quota_policy()
    hour = policy.daily_generation_reset_hour if reset_hour is None else reset_hour
    local_now = _aware_utc(now).astimezone(SHANGHAI_TZ)
    local_start = datetime.combine(local_now.date(), time(hour=hour), tzinfo=SHANGHAI_TZ)
    if local_now < local_start:
        local_start -= timedelta(days=1)
    local_reset = local_start + timedelta(days=1)
    return local_start.astimezone(UTC), local_reset


def effective_daily_generation_limit(user: User, policy: QuotaPolicy | None = None) -> int:
    active_policy = policy or get_quota_policy()
    if user.daily_generation_limit_override is not None:
        return user.daily_generation_limit_override
    return active_policy.daily_generation_limit


def _exempt_state(user: User, policy: QuotaPolicy, now: datetime | None = None) -> GenerationQuotaState:
    window_start_at, reset_at = current_generation_quota_window(now, policy.daily_generation_reset_hour)
    limit = effective_daily_generation_limit(user, policy)
    return GenerationQuotaState(
        limit=limit,
        used=0,
        remaining=limit,
        reset_at=reset_at,
        window_start_at=window_start_at,
        exempt=True,
    )


async def _get_or_create_window(
    session: AsyncSession,
    user_id: str,
    window_start_at: datetime,
) -> tuple[UserGenerationQuotaWindow, bool]:
    result = await session.execute(
        select(UserGenerationQuotaWindow)
        .where(UserGenerationQuotaWindow.user_id == user_id)
        .where(UserGenerationQuotaWindow.window_start_at == window_start_at)
        .with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row, False

    row = UserGenerationQuotaWindow(
        user_id=user_id,
        window_start_at=window_start_at,
        used_count=0,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        result = await session.execute(
            select(UserGenerationQuotaWindow)
            .where(UserGenerationQuotaWindow.user_id == user_id)
            .where(UserGenerationQuotaWindow.window_start_at == window_start_at)
            .with_for_update()
        )
        row = result.scalar_one()
        return row, False
    return row, True


async def _ensure_window_and_balance(
    session: AsyncSession,
    user: User,
    limit: int,
    window_start_at: datetime,
) -> UserGenerationQuotaWindow:
    row, created = await _get_or_create_window(session, user.id, window_start_at)
    if created:
        current_balance = int(user.daily_generation_balance or 0)
        if current_balance < limit:
            user.daily_generation_balance = limit
        elif user.daily_generation_balance is None:
            user.daily_generation_balance = current_balance
        user.updated_at = utc_now()
        await session.flush()
    elif user.daily_generation_balance is None:
        user.daily_generation_balance = max(limit - int(row.used_count), 0)
        user.updated_at = utc_now()
        await session.flush()
    return row


def _balance(user: User) -> int:
    return max(int(user.daily_generation_balance or 0), 0)


def _used_from_balance(limit: int, balance: int) -> int:
    return max(limit - min(balance, limit), 0)


async def require_legacy_quota_writer(session: AsyncSession, user: User) -> User:
    locked = await account_lock(session, user.id)
    wallet = await wallet_if_installed(session, user.id, lock=True)
    if wallet is not None and wallet.write_owner != "legacy":
        raise HTTPException(status_code=409, detail={"code": "v2_wallet_required"})
    return locked


async def get_generation_quota_state(
    session: AsyncSession,
    user: User,
    now: datetime | None = None,
) -> GenerationQuotaState:
    user = await account_lock(session, user.id)
    wallet = await wallet_if_installed(session, user.id, lock=True)
    if wallet is not None and wallet.write_owner == "v2":
        from app.quota.policy import top_up
        return GenerationQuotaState(**await top_up(session, wallet, now=now))
    policy = get_quota_policy()
    if user.role == UserRole.admin:
        return _exempt_state(user, policy, now)
    window_start_at, reset_at = current_generation_quota_window(now, policy.daily_generation_reset_hour)
    limit = effective_daily_generation_limit(user, policy)
    try:
        row = await _ensure_window_and_balance(session, user, limit, window_start_at)
    except SQLAlchemyError as exc:
        logger.exception("Failed to read generation quota state")
        raise HTTPException(status_code=503, detail="generation_quota_unavailable") from exc
    balance = _balance(user)
    return GenerationQuotaState(
        limit=limit,
        used=_used_from_balance(limit, balance),
        remaining=balance,
        reset_at=reset_at,
        window_start_at=window_start_at,
    )


async def consume_generation_quota(
    session: AsyncSession,
    user: User,
    now: datetime | None = None,
) -> GenerationQuotaState:
    user = await require_legacy_quota_writer(session, user)
    policy = get_quota_policy()
    if user.role == UserRole.admin:
        return _exempt_state(user, policy, now)
    window_start_at, reset_at = current_generation_quota_window(now, policy.daily_generation_reset_hour)
    limit = effective_daily_generation_limit(user, policy)
    try:
        row = await _ensure_window_and_balance(session, user, limit, window_start_at)
        balance = _balance(user)
        if balance <= 0:
            state = GenerationQuotaState(
                limit=limit,
                used=_used_from_balance(limit, balance),
                remaining=0,
                reset_at=reset_at,
                window_start_at=window_start_at,
            )
            raise GenerationDailyQuotaExceeded(state)
        user.daily_generation_balance = balance - 1
        user.updated_at = utc_now()
        row.used_count = int(row.used_count) + 1
        row.updated_at = utc_now()
        await session.flush()
    except GenerationDailyQuotaExceeded:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to consume generation quota")
        raise HTTPException(status_code=503, detail="generation_quota_unavailable") from exc
    return GenerationQuotaState(
        limit=limit,
        used=_used_from_balance(limit, _balance(user)),
        remaining=_balance(user),
        reset_at=reset_at,
        window_start_at=window_start_at,
    )


async def refund_generation_quota(
    session: AsyncSession,
    user_id: str,
    window_start_at: datetime,
    *, legacy_job_id: str | None = None,
) -> bool:
    try:
        user = await account_lock(session, user_id)
        wallet = await wallet_if_installed(session, user_id, lock=True)
        if wallet is not None and wallet.write_owner == "v2":
            return await legacy_refund(session, wallet, job_id=legacy_job_id) if legacy_job_id else False
        if user.role == UserRole.admin:
            return False
        result = await session.execute(
            select(UserGenerationQuotaWindow)
            .where(UserGenerationQuotaWindow.user_id == user_id)
            .where(UserGenerationQuotaWindow.window_start_at == window_start_at)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None or int(row.used_count) <= 0:
            return False
        row.used_count = int(row.used_count) - 1
        row.updated_at = utc_now()
        user.daily_generation_balance = _balance(user) + 1
        user.updated_at = utc_now()
        await session.flush()
        return True
    except ApplicationError as exc:
        if exc.code == "not_found":
            return False
        raise
    except SQLAlchemyError:
        logger.exception("Failed to refund generation quota user_id=%s", user_id)
        return False


async def reset_generation_quota(
    session: AsyncSession,
    user_id: str | None = None,
    now: datetime | None = None,
) -> GenerationQuotaResetResult:
    policy = get_quota_policy()
    window_start_at, _ = current_generation_quota_window(now, policy.daily_generation_reset_hour)
    statement = delete(UserGenerationQuotaWindow).where(
        UserGenerationQuotaWindow.window_start_at == window_start_at
    )
    if user_id is not None:
        user = await account_lock(session, user_id)
        await require_legacy_quota_writer(session, user)
        statement = statement.where(UserGenerationQuotaWindow.user_id == user_id)
    elif (await session.execute(text("SELECT to_regclass('public.v2_wallets')"))).scalar_one_or_none():
        from app.design.models import Wallet
        statement = statement.where(UserGenerationQuotaWindow.user_id.not_in(
            select(Wallet.owner_id).where(Wallet.write_owner != "legacy")))
    try:
        result = await session.execute(statement)
        await session.flush()
    except SQLAlchemyError as exc:
        logger.exception("Failed to reset generation quota")
        raise HTTPException(status_code=503, detail="generation_quota_unavailable") from exc
    return GenerationQuotaResetResult(
        reset_count=int(result.rowcount or 0),
        window_start_at=window_start_at,
    )


async def adjust_generation_quota_balance(
    session: AsyncSession,
    user: User,
    delta: int,
) -> GenerationQuotaBalanceAdjustResult:
    user = await require_legacy_quota_writer(session, user)
    try:
        current_balance = _balance(user)
        user.daily_generation_balance = max(current_balance + delta, 0)
        user.updated_at = utc_now()
        await session.flush()
    except SQLAlchemyError as exc:
        logger.exception("Failed to adjust generation quota balance")
        raise HTTPException(status_code=503, detail="generation_quota_unavailable") from exc
    return GenerationQuotaBalanceAdjustResult(balance=_balance(user))
