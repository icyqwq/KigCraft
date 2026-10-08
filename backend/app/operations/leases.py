from __future__ import annotations
from datetime import timedelta
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.time import utc_now
from app.design.models import Branch, CodexEpoch, Design, Operation
from app.conversation.checkpoints import checkpoint
from app.design.service import ApplicationError, event, owned_design


async def locked_operation(db: AsyncSession, operation_id: str, owner_id: str | None = None) -> Operation:
    row = (await db.execute(select(Operation.design_id, Operation.owner_id).where(
        Operation.id == operation_id
    ))).first()
    if row is None or (owner_id is not None and row.owner_id != owner_id):
        raise ApplicationError("not_found")
    await owned_design(db, row.owner_id, row.design_id, lock=True)
    return (await db.execute(select(Operation).where(Operation.id == operation_id).with_for_update())).scalar_one()


async def claim(db: AsyncSession, worker_id: str, *, lease_seconds: int = 90, owner_id: str | None = None) -> Operation | None:
    if not worker_id or lease_seconds <= 0:
        raise ValueError("Invalid worker lease")
    from app.admin.v2_controls import gate
    if owner_id:
        try:
            await gate(db,owner_id)
        except ApplicationError as exc:
            if exc.code in {"generation_paused","runtime_quarantined"}:
                return None
            raise
    # The native image tool has no verified per-turn call cap in the pinned runtime.
    # Local trials therefore admit one operation across this database. A transaction
    # lock serializes the read-and-claim decision across processes and account pins.
    # Try-lock avoids waiting while another worker holds the admission lock.
    if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"),
                           {"key": 7272192216}):
        return None
    now = utc_now()
    # Lock design before operation, matching cancellation/publication and snapshot readers.
    operation_id = (await db.execute(select(Operation.id).join(Design, Design.id == Operation.design_id).where(
        Operation.state == "queued",
        Operation.owner_id == owner_id if owner_id else True,
        or_(Operation.lease_expires_at.is_(None), Operation.lease_expires_at <= now),
    ).order_by(Operation.created_at, Operation.id).limit(1)
        .with_for_update(of=Design, skip_locked=True))).scalar_one_or_none()
    if operation_id is None:
        return None
    operation = (await db.execute(select(Operation).where(
        Operation.id == operation_id, Operation.state == "queued"
    ).with_for_update())).scalar_one_or_none()
    if operation is None:
        return None
    try:
        await gate(db,operation.owner_id)
    except ApplicationError as exc:
        if exc.code in {"generation_paused","runtime_quarantined"}:
            return None
        raise
    if operation.dispatch_intent_at is not None:
        operation.state = "reconciling"
        operation.error_code = "upstream_status_unknown"
        await db.flush()
        return None
    occupied = await db.scalar(select(Operation.id).where(
        Operation.id != operation.id,
        Operation.upstream_status.is_(None),
        or_(
            Operation.state.in_(["reviewing", "running", "cancelling", "reconciling"]),
            Operation.dispatch_intent_at.is_not(None),
        ),
    ).limit(1))
    if occupied is not None:
        # Expiry, cancellation, publication and worker restart do not prove that an
        # upstream turn ended. Only verified terminal evidence frees a dispatched slot.
        return None
    operation.lease_owner = worker_id
    operation.lease_expires_at = now + timedelta(seconds=lease_seconds)
    operation.fencing_token += 1
    operation.state = "reviewing"
    operation.updated_at = now
    await event(db, operation.design_id, "operation.state_changed",
                {"operation_id": operation.id, "state": operation.state})
    await db.flush()
    return operation


def check_fence(operation: Operation, worker_id: str, token: int) -> None:
    if (operation.lease_owner != worker_id or operation.fencing_token != token
            or operation.lease_expires_at is None or operation.lease_expires_at <= utc_now()):
        raise ApplicationError("stale_lease")


async def heartbeat(db: AsyncSession, operation_id: str, worker_id: str, token: int,
                    *, lease_seconds: int = 90) -> None:
    operation = await locked_operation(db, operation_id)
    check_fence(operation, worker_id, token)
    if lease_seconds <= 0:
        raise ValueError("Invalid lease duration")
    operation.lease_expires_at = utc_now() + timedelta(seconds=lease_seconds)
    await db.flush()


async def record_dispatch_intent(db: AsyncSession, operation_id: str, worker_id: str,
                                 token: int, thread_id: str) -> None:
    operation = await locked_operation(db, operation_id)
    check_fence(operation, worker_id, token)
    from app.admin.v2_controls import gate
    await gate(db,operation.owner_id)
    if operation.state != "reviewing" or operation.dispatch_intent_at is not None:
        raise ApplicationError("upstream_status_unknown")
    branch = await db.get(Branch, operation.branch_id)
    epoch = await db.get(CodexEpoch, branch.active_epoch_id) if branch.active_epoch_id else None
    if operation.context_before is None and epoch and epoch.thread_id == thread_id:
        # A newly forked thread inherits its source turn, which has no operation on the child thread yet.
        own_checkpoint = (await db.execute(select(Operation.id).where(
            Operation.upstream_thread_id == epoch.thread_id,
            Operation.upstream_turn_id == epoch.checkpoint_turn_id,
            Operation.upstream_status.in_(["completed", "failed", "interrupted"]),
            Operation.design_id == operation.design_id,
        ))).first() if epoch.checkpoint_turn_id else None
        if (not own_checkpoint and branch.context_seed and branch.context_seed.get("kind") == "turn"
                and branch.context_seed.get("turn_id") == epoch.checkpoint_turn_id):
            operation.context_before = branch.context_seed
        else:
            operation.context_before = checkpoint(epoch, epoch.checkpoint_turn_id)
    operation.upstream_thread_id = thread_id
    operation.dispatch_intent_at = utc_now()
    operation.state = "running"
    await event(db, operation.design_id, "operation.state_changed",
                {"operation_id": operation.id, "state": operation.state})
    # Caller commits this transaction BEFORE sending turn/start.


async def recover_expired(db: AsyncSession, operation_id: str) -> str:
    operation = await locked_operation(db, operation_id)
    if (operation.lease_expires_at is None or operation.lease_expires_at > utc_now()
            or operation.state not in {"reviewing", "running", "publishing", "cancelling"}):
        return operation.state
    operation.fencing_token += 1
    operation.lease_owner = None
    operation.lease_expires_at = None
    if operation.upstream_status is not None:
        operation.state = "publishing"
        operation.error_code = "publication_pending"
    elif operation.dispatch_intent_at is not None or operation.state == "cancelling":
        operation.state = "reconciling"
        operation.error_code = "upstream_status_unknown"
    else:
        operation.state = "queued"
    await event(db, operation.design_id, "operation.state_changed",
                {"operation_id": operation.id, "state": operation.state})
    return operation.state
