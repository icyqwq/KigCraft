from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.design.models import LedgerEntry
from app.design.service import ApplicationError
from app.operations.leases import locked_operation
from app.quota.cutover import require_actor
from app.quota.wallet import entry, locked_wallet
from app.quota.policy import top_up


async def adjust(db: AsyncSession, owner_id: str, *, delta: int, key: str, actor_id: str, reason: str):
    await require_actor(db, actor_id, reason)
    if type(delta) is not int or delta == 0 or abs(delta) > 100000 or not 8 <= len(key) <= 100:
        raise ApplicationError("invalid_adjustment")
    wallet = await locked_wallet(db, owner_id)
    await top_up(db, wallet)
    return await entry(db, wallet, key=f"adjust:{key}", kind="adjust",
                       balance_delta=delta, reserved_delta=0,
                       actor_id=actor_id, details={"reason": reason})


async def refund(db: AsyncSession, operation_id: str, *, units: int, key: str, actor_id: str, reason: str):
    await require_actor(db, actor_id, reason)
    if type(units) is not int or units <= 0 or not 8 <= len(key) <= 100:
        raise ApplicationError("invalid_refund")
    operation = await locked_operation(db, operation_id)
    wallet = await locked_wallet(db, operation.owner_id)
    await top_up(db, wallet)
    existing = (await db.execute(select(LedgerEntry).where(
        LedgerEntry.owner_id == operation.owner_id, LedgerEntry.idempotency_key == f"refund:{key}"
    ))).scalar_one_or_none()
    if existing is None:
        refunded = (await db.execute(select(func.coalesce(func.sum(LedgerEntry.balance_delta), 0)).where(
            LedgerEntry.operation_id == operation_id, LedgerEntry.kind == "refund"
        ))).scalar_one()
        if refunded + units > operation.settled_units:
            raise ApplicationError("refund_exceeds_settlement")
    return await entry(db, wallet, key=f"refund:{key}", kind="refund",
                       balance_delta=units, reserved_delta=0, operation_id=operation_id,
                       actor_id=actor_id, details={"reason": reason})
