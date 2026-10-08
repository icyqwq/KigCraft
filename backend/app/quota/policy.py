"""One daily top-up of the balance. Generations are charged up front and unused units are returned."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.quota.settings import get_quota_policy
from app.auth.models import UserRole
from app.db.models import User
from app.design.models import LedgerEntry, Wallet
from app.design.service import ApplicationError
from app.quota.wallet import entry


async def top_up(db: AsyncSession, wallet: Wallet, *, now: datetime | None = None):
    # Caller holds the wallet lock; reads of the legacy user policy do not acquire another lock.
    from app.generation.quota import current_generation_quota_window, effective_daily_generation_limit
    user = await db.get(User, wallet.owner_id)
    if user is None:
        raise ApplicationError("not_found")
    policy = get_quota_policy()
    start, reset = current_generation_quota_window(now, policy.daily_generation_reset_hour)
    limit = effective_daily_generation_limit(user, policy)
    if wallet.last_topup_window is None or wallet.last_topup_window < start:
        # Returning yesterday's charge after a failure cannot lift the balance above the daily limit.
        await entry(db, wallet, key=f"topup:{start.isoformat()}", kind="topup",
                    balance_delta=max(0, limit - wallet.balance), reserved_delta=0,
                    details={"window_start": start.isoformat(), "target": limit})
        wallet.last_topup_window = start
        await db.flush()
    used = -(await db.execute(select(func.coalesce(func.sum(LedgerEntry.balance_delta), 0)).where(
        LedgerEntry.owner_id == wallet.owner_id, LedgerEntry.kind.in_(("settle", "charge", "release")),
        LedgerEntry.created_at >= start, LedgerEntry.created_at < reset,
    ))).scalar_one()
    return {"limit": limit, "used": used, "remaining": wallet.balance - wallet.reserved,
            "window_start_at": start, "reset_at": reset, "exempt": user.role == UserRole.admin}
