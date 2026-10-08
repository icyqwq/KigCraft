from sqlalchemy.ext.asyncio import AsyncSession
from app.design.service import event
from app.operations.leases import locked_operation
from app.operations.service import TERMINAL
from app.quota.wallet import release


async def cancel(db: AsyncSession, owner_id: str, operation_id: str) -> str:
    operation = await locked_operation(db, operation_id, owner_id)
    if operation.state in TERMINAL or operation.state == "cancelling":
        return operation.state
    operation.cancel_requested = True
    if operation.kind == "viewset":
        from app.quota.viewsets import binding
        group, _, _ = await binding(db, operation)
        group.cancel_requested = True
    if operation.dispatch_intent_at is None:
        await release(db, operation)
        operation.state = "cancelled"
        operation.fencing_token += 1
        operation.lease_owner = None
        operation.lease_expires_at = None
    else:
        # The runtime still needs turn/interrupt; unknown results may not be charged/published.
        operation.state = "cancelling"
    await event(db, operation.design_id, "operation.state_changed",
                {"operation_id": operation.id, "state": operation.state})
    from app.operations.viewset_group import operation_finished
    await operation_finished(db, operation)
    return operation.state
