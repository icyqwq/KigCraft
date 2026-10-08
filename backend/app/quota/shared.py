"""Shared quota: V2 spends the same daily balance as V1 (one number per account, e.g. 20 a day).

Used when both versions run side by side on one database. The legacy balance is the single ledger: V2 takes a unit when
an operation is accepted and gives back whatever the delivery did not use, exactly as V1 does for its own jobs.
"""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.models import UserRole
from app.core.config import get_settings
from app.db.models import User
from app.design.models import Operation
from app.design.service import ApplicationError


def enabled() -> bool:
    return get_settings().v2_quota_mode == "shared_legacy"


async def reserve(db: AsyncSession, operation: Operation) -> None:
    from app.generation.quota import GenerationDailyQuotaExceeded, consume_generation_quota
    if operation.reserved_units <= 0:
        return
    user = await db.get(User, operation.owner_id)
    if user is None:
        raise ApplicationError("not_found")
    try:
        for _ in range(operation.reserved_units):
            await consume_generation_quota(db, user)
    except GenerationDailyQuotaExceeded:
        raise ApplicationError("quota_insufficient") from None


async def settle(db: AsyncSession, operation: Operation, units: int) -> None:
    if operation.reservation_closed or operation.settled_units + units > operation.reserved_units:
        raise ApplicationError("authorization_exceeded")
    operation.settled_units += units
    await db.flush()


async def release(db: AsyncSession, operation: Operation) -> None:
    from app.generation.quota import current_generation_quota_window, refund_generation_quota
    from app.quota.settings import get_quota_policy
    remainder = operation.reserved_units - operation.settled_units
    user = await db.get(User, operation.owner_id)
    if remainder > 0 and user is not None and user.role != UserRole.admin:
        # The charge belongs to the window it was taken in, which is the one containing the operation's creation.
        start, _ = current_generation_quota_window(operation.created_at, get_quota_policy().daily_generation_reset_hour)
        for _ in range(remainder):
            await refund_generation_quota(db, operation.owner_id, start)
    operation.reservation_closed = True
    await db.flush()
