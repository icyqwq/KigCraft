"""One up-front charge per view-set run; units not delivered are refunded."""
from sqlalchemy import select
from app.design.models import LedgerEntry, ViewSet, ViewSetItem, ViewSetAttempt
from app.design.service import ApplicationError
from app.quota.wallet import capped_refund, entry, locked_wallet
from app.quota.policy import top_up


async def binding(db, operation):
    row = (await db.execute(select(ViewSet, ViewSetItem, ViewSetAttempt)
        .join(ViewSetItem, ViewSetItem.viewset_id == ViewSet.id)
        .join(ViewSetAttempt, ViewSetAttempt.item_id == ViewSetItem.id)
        .where(ViewSetAttempt.operation_id == operation.id))).first()
    if row is None or row[0].owner_id != operation.owner_id or row[0].design_id != operation.design_id:
        raise ApplicationError("viewset_binding_unavailable")
    return row


async def hold(db, group, units):
    if group.held_units or units < 0 or group.settled_units + units > group.max_units:
        raise ApplicationError("authorization_exceeded")
    wallet = await locked_wallet(db, group.owner_id)
    await top_up(db, wallet)
    group.held_units = units
    await entry(db, wallet, key=f"viewset-reserve:{group.id}:{group.run_number}", kind="charge",
        balance_delta=-units, reserved_delta=0, details={"viewset_id":group.id,"run":group.run_number})


async def settle_view(db, operation, delivery_key, units):
    group, item, attempt = await binding(db, operation)
    if (delivery_key != "main_image" or units != item.unit_cost or item.view == "front"
            or attempt.number != item.attempt_count or not item.revision_id):
        raise ApplicationError("unauthorized_delivery")
    wallet = await locked_wallet(db, operation.owner_id)
    key = f"viewset-settle:{group.id}:{item.view}"
    previous = await db.scalar(select(LedgerEntry).where(
        LedgerEntry.owner_id == operation.owner_id, LedgerEntry.idempotency_key == key))
    if previous:
        if previous.operation_id != operation.id or (previous.details or {}).get("units", -previous.balance_delta) != units:
            raise ApplicationError("delivery_identity_conflict")
        return
    if (operation.reservation_closed or group.cancel_requested or operation.cancel_requested
            or group.held_units < units or group.settled_units + units > group.max_units
            or operation.settled_units + units > operation.reserved_units):
        raise ApplicationError("authorization_exceeded")
    await top_up(db, wallet)
    group.held_units -= units
    group.settled_units += units
    operation.settled_units += units
    await entry(db, wallet, key=key, kind="settle", balance_delta=0, reserved_delta=0,
        operation_id=operation.id, details={"viewset_id":group.id,"view":item.view,"units":units})


async def release_hold(db, group):
    key = f"viewset-release:{group.id}:{group.run_number}"
    previous = await db.scalar(select(LedgerEntry).where(
        LedgerEntry.owner_id == group.owner_id, LedgerEntry.idempotency_key == key))
    if previous:
        if group.held_units:
            raise ApplicationError("ledger_idempotency_conflict")
        return
    wallet = await locked_wallet(db, group.owner_id)
    window = await top_up(db, wallet)
    units = await capped_refund(db, wallet, window, f"viewset-reserve:{group.id}:{group.run_number}", group.held_units)
    group.held_units = 0
    await entry(db, wallet, key=f"viewset-release:{group.id}:{group.run_number}", kind="release",
        balance_delta=units, reserved_delta=0, details={"viewset_id":group.id,"run":group.run_number})
