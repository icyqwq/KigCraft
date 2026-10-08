"""Allocate a historical context outside DB transactions with durable no-replay intent."""
from __future__ import annotations
from app.codex_runtime.launch import DESIGN_MODEL
from app.conversation.checkpoints import resolve_boundary
from app.core.time import utc_now
from app.design.models import Branch, CodexEpoch
from app.design.service import ApplicationError
from app.operations.leases import check_fence, locked_operation

async def allocate(worker, rpc, operation_id: str, token: int, runtime_identity: str):
    async with worker.factory.begin() as db:
        operation = await locked_operation(db, operation_id)
        check_fence(operation, worker.worker_id, token)
        allocation = operation.context_allocation
        branch = await db.get(Branch, operation.branch_id)
        if not allocation or allocation.get("state") != "pending" or branch.active_epoch_id:
            raise ApplicationError("context_allocation_unknown")
        boundary = await resolve_boundary(db, operation.design_id, operation.context_before)
        if boundary.thread_id and (boundary.runtime_identity != runtime_identity
                or boundary.codex_version != worker.runtime.version):
            raise ApplicationError("runtime_binding_unavailable")
        operation.context_allocation = {**allocation, "state":"sent", "sent_at":utc_now().isoformat()}
    # The transaction above commits before any RPC. Never repeat a sent allocation after a crash.
    from app.operations.context_observations import open_context
    session = await open_context(worker, rpc, operation_id, token, thread_id=boundary.thread_id, fork_after=boundary.turn_id)
    async with worker.factory.begin() as db:
        operation = await locked_operation(db, operation_id)
        check_fence(operation, worker.worker_id, token)
        branch = await db.get(Branch, operation.branch_id)
        if branch.active_epoch_id:
            raise ApplicationError("runtime_binding_changed")
        epoch = CodexEpoch(branch_id=branch.id, thread_id=session.binding.thread_id,
            runtime_identity=runtime_identity, model=DESIGN_MODEL, codex_version=worker.runtime.version,
            checkpoint_turn_id=boundary.turn_id)
        db.add(epoch)
        await db.flush()
        branch.active_epoch_id = epoch.id
        operation.context_allocation = {**operation.context_allocation,"state":"bound","epoch_id":epoch.id}
    return session, epoch
