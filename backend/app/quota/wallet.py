from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.design.models import LedgerEntry, Operation, Wallet
from app.design.service import ApplicationError


async def locked_wallet(db: AsyncSession, owner_id: str) -> Wallet:
    # A prior read in this session may predate another transaction's grant or settlement.
    wallet = (await db.execute(select(Wallet).where(Wallet.owner_id == owner_id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if wallet is None or wallet.write_owner != "v2":
        raise ApplicationError("wallet_not_migrated")
    return wallet


async def entry(db: AsyncSession, wallet: Wallet, *, key: str, kind: str,
                balance_delta: int, reserved_delta: int, operation_id: str | None = None,
                actor_id: str | None = None, details: dict | None = None) -> LedgerEntry:
    existing = (await db.execute(select(LedgerEntry).where(
        LedgerEntry.owner_id == wallet.owner_id, LedgerEntry.idempotency_key == key
    ))).scalar_one_or_none()
    if existing is not None:
        if (existing.kind, existing.balance_delta, existing.reserved_delta, existing.operation_id, existing.actor_id, existing.details) != (
            kind, balance_delta, reserved_delta, operation_id, actor_id, details or {}
        ):
            raise ApplicationError("ledger_idempotency_conflict")
        return existing
    balance, reserved = wallet.balance + balance_delta, wallet.reserved + reserved_delta
    if not balance >= reserved >= 0:
        raise ApplicationError("quota_insufficient")
    wallet.balance, wallet.reserved = balance, reserved
    wallet.version += 1
    record = LedgerEntry(owner_id=wallet.owner_id, operation_id=operation_id, idempotency_key=key,
                         kind=kind, balance_delta=balance_delta, reserved_delta=reserved_delta,
                         balance_after=balance, reserved_after=reserved, actor_id=actor_id, details=details or {})
    db.add(record)
    await db.flush()
    return record


async def capped_refund(db: AsyncSession, wallet: Wallet, window: dict, charge_key: str, units: int) -> int:
    # A charge taken before today's top-up was already covered by that top-up; returning it must not exceed the limit.
    charged_at = await db.scalar(select(LedgerEntry.created_at).where(
        LedgerEntry.owner_id == wallet.owner_id, LedgerEntry.idempotency_key == charge_key))
    if charged_at is not None and charged_at < window["window_start_at"]:
        return max(0, min(units, window["limit"] - wallet.balance))
    return units


async def reserve(db: AsyncSession, operation: Operation) -> None:
    if operation.kind == "viewset":
        raise ApplicationError("viewset_group_reservation_required")
    from app.quota import shared
    if shared.enabled():
        return await shared.reserve(db, operation)
    wallet = await locked_wallet(db, operation.owner_id)
    from app.quota.policy import top_up
    await top_up(db, wallet)
    # Charged up front; release() returns whatever the delivery did not use.
    await entry(db, wallet, key=f"charge:{operation.id}", kind="charge",
                balance_delta=-operation.reserved_units, reserved_delta=0, operation_id=operation.id)


async def settle(db: AsyncSession, operation: Operation, delivery_key: str, units: int) -> None:
    if not isinstance(units, int) or isinstance(units, bool) or units < 0:
        raise ApplicationError("invalid_delivery_units")
    if operation.kind == "viewset":
        from app.quota.viewsets import settle_view
        return await settle_view(db, operation, delivery_key, units)
    from app.quota import shared
    if shared.enabled():
        return await shared.settle(db, operation, units)
    wallet = await locked_wallet(db, operation.owner_id)
    from app.quota.policy import top_up
    await top_up(db, wallet)
    key = f"settle:{operation.id}:{delivery_key}"
    previous = (await db.execute(select(LedgerEntry).where(
        LedgerEntry.owner_id == operation.owner_id, LedgerEntry.idempotency_key == key
    ))).scalar_one_or_none()
    if previous is not None:
        if (previous.details or {}).get("units", -previous.balance_delta) != units:
            raise ApplicationError("ledger_idempotency_conflict")
        return
    if operation.reservation_closed or operation.settled_units + units > operation.reserved_units:
        raise ApplicationError("authorization_exceeded")
    # The charge was taken at submit; a delivery only records that its units were used.
    await entry(db, wallet, key=key, kind="settle", balance_delta=0, reserved_delta=0,
                operation_id=operation.id, details={"units": units})
    operation.settled_units += units
    await db.flush()


async def release(db: AsyncSession, operation: Operation) -> None:
    if operation.reservation_closed:
        return
    if operation.kind == "viewset":
        # The group owns the hold; terminating one attempt must not release another view's authorization.
        operation.reservation_closed = True
        await db.flush()
        return
    from app.quota import shared
    if shared.enabled():
        return await shared.release(db, operation)
    wallet = await locked_wallet(db, operation.owner_id)
    from app.quota.policy import top_up
    window = await top_up(db, wallet)
    remainder = await capped_refund(db, wallet, window, f"charge:{operation.id}",
                                    operation.reserved_units - operation.settled_units)
    await entry(db, wallet, key=f"release:{operation.id}", kind="release",
                balance_delta=remainder, reserved_delta=0, operation_id=operation.id)
    operation.reservation_closed = True
    await db.flush()
