"""Per-account freeze/drain/opening. Callers own the transaction; no task cancellation or replay."""
from __future__ import annotations
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.models import UserRole
from app.db.models import GenerationJob, User
from app.design.models import LedgerEntry, Wallet
from app.design.service import ApplicationError
from app.quota.wallet import entry

LEGACY_TERMINAL = {"succeeded", "failed", "cancelled", "expired"}


async def account_lock(db: AsyncSession, user_id: str) -> User:
    # NO KEY UPDATE permits a legacy job's FK insert in its repository transaction.
    # It still serializes quota writers and cutover without self-deadlocking the old POST.
    user = (await db.execute(select(User).where(User.id == user_id)
        .with_for_update(key_share=True).execution_options(populate_existing=True))).scalar_one_or_none()
    if user is None:
        raise ApplicationError("not_found")
    return user


async def wallet_if_installed(db: AsyncSession, owner_id: str, *, lock: bool = False) -> Wallet | None:
    if (await db.execute(text("SELECT to_regclass('public.v2_wallets')"))).scalar_one_or_none() is None:
        return None
    query = select(Wallet).where(Wallet.owner_id == owner_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await db.execute(query)).scalar_one_or_none()


async def require_actor(db: AsyncSession, actor_id: str, reason: str):
    actor = await db.get(User, actor_id)
    if actor is None or actor.is_banned or actor.role != UserRole.admin:
        raise ApplicationError("admin_required")
    if not reason.strip() or len(reason) > 1000:
        raise ApplicationError("reason_required")


async def freeze(db: AsyncSession, owner_id: str, *, actor_id: str, reason: str) -> Wallet:
    await require_actor(db, actor_id, reason)
    await account_lock(db, owner_id)
    wallet = await wallet_if_installed(db, owner_id, lock=True)
    if wallet and wallet.write_owner == "v2":
        return wallet
    if wallet is None:
        wallet = Wallet(owner_id=owner_id, balance=0, reserved=0, version=0, write_owner="frozen")
        db.add(wallet)
        await db.flush()
    if wallet.write_owner not in {"legacy", "frozen"}:
        raise ApplicationError("wallet_owner_invalid")
    wallet.write_owner = "frozen"
    await entry(db, wallet, key="cutover:freeze", kind="freeze", balance_delta=0, reserved_delta=0,
                actor_id=actor_id, details={"reason": reason})
    return wallet


async def cut_over(db: AsyncSession, owner_id: str, *, actor_id: str, reason: str) -> Wallet:
    await require_actor(db, actor_id, reason)
    user = await account_lock(db, owner_id)
    wallet = await wallet_if_installed(db, owner_id, lock=True)
    if wallet is None or wallet.write_owner not in {"frozen", "v2"}:
        raise ApplicationError("freeze_required")
    if wallet.write_owner == "v2":
        return wallet
    active = (await db.execute(select(GenerationJob.id).where(
        GenerationJob.user_id == owner_id, GenerationJob.runtime_status.not_in(LEGACY_TERMINAL)
    ).limit(1))).first()
    if active:
        raise ApplicationError("legacy_jobs_active")
    # Resolve the old policy exactly once after new legacy submissions have been frozen.
    from app.generation.quota import (
        _ensure_window_and_balance, current_generation_quota_window, effective_daily_generation_limit,
    )
    start, _ = current_generation_quota_window()
    await _ensure_window_and_balance(db, user, effective_daily_generation_limit(user), start)
    balance = max(0, int(user.daily_generation_balance or 0))
    if wallet.balance or wallet.reserved:
        raise ApplicationError("opening_balance_conflict")
    await entry(db, wallet, key="cutover:opening", kind="opening", balance_delta=balance, reserved_delta=0,
                actor_id=actor_id, details={"reason": reason, "legacy_balance": balance,
                                           "window_start": start.isoformat()})
    wallet.last_topup_window = start
    wallet.write_owner = "v2"
    await db.flush()
    return wallet


async def legacy_refund(db: AsyncSession, wallet: Wallet, *, job_id: str) -> bool:
    # A late legacy result must identify its original task, never only its user/day.
    job = (await db.execute(select(GenerationJob).where(
        GenerationJob.id == job_id, GenerationJob.user_id == wallet.owner_id
    ))).scalar_one_or_none()
    reservation = (job.prompt_payload or {}).get("quota_reservation") if job else None
    if not isinstance(reservation, dict) or reservation.get("refunded"):
        return False
    if not isinstance(reservation.get("window_start_at"), str):
        return False
    from app.quota.policy import top_up
    await top_up(db, wallet)
    await entry(db, wallet, key=f"legacy-refund:{job_id}", kind="legacy_refund",
                balance_delta=1, reserved_delta=0,
                details={"legacy_job_id": job_id, "window_start": reservation["window_start_at"]})
    return True
