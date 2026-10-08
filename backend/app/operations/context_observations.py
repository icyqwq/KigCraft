"""Private, fenced receipts for main-session opens; never infer old successes."""
from sqlalchemy import select, func
from app.codex_runtime.session import CodexSession
from app.core.time import utc_now
from app.design.models import ContextObservation, Operation
from app.design.service import ApplicationError
from app.operations.leases import locked_operation, check_fence


async def open_context(worker, rpc, operation_id, token, *, thread_id=None, fork_after=None):
    method = "fork" if fork_after else "resume" if thread_id else "start"
    async with worker.factory.begin() as db:
        operation = await locked_operation(db, operation_id)
        check_fence(operation, worker.worker_id, token)
        old = await db.scalar(select(ContextObservation).where(
            ContextObservation.operation_id == operation_id,
            ContextObservation.fencing_token == token))
        if old is not None:
            raise ApplicationError("context_open_already_observed")
        row = ContextObservation(operation_id=operation_id, fencing_token=token,
                                 method=method, state="sent")
        db.add(row)
        await db.flush()
        identity = row.id
    # Intent is durable before the RPC; interrupted processes leave an unconfirmed receipt.
    try:
        session = await CodexSession.open(rpc, thread_id=thread_id, fork_after=fork_after)
    except Exception:
        await finish(worker, operation_id, token, identity, "unconfirmed")
        raise
    await finish(worker, operation_id, token, identity, "confirmed")
    return session


async def finish(worker, operation_id, token, identity, state):
    async with worker.factory.begin() as db:
        operation = await locked_operation(db, operation_id)
        check_fence(operation, worker.worker_id, token)
        row = await db.get(ContextObservation, identity)
        if row is None or row.operation_id != operation_id or row.state != "sent":
            raise ApplicationError("context_observation_conflict")
        row.state = state
        row.completed_at = utc_now()


async def totals(db):
    rows = (await db.execute(select(ContextObservation.method, ContextObservation.state, func.count())
            .group_by(ContextObservation.method, ContextObservation.state))).all()
    counts = {(method, state): count for method, state, count in rows}
    methods = {}
    for method in ("start", "resume", "fork"):
        confirmed = counts.get((method, "confirmed"), 0)
        unconfirmed = counts.get((method, "sent"), 0) + counts.get((method, "unconfirmed"), 0)
        attempts = confirmed + unconfirmed
        methods[method] = {"attempts": attempts, "confirmed": confirmed, "unconfirmed": unconfirmed,
                           "confirmation_rate": confirmed / attempts if attempts else None}
    without = await db.scalar(select(func.count()).select_from(Operation).where(
        Operation.dispatch_intent_at.is_not(None),
        ~select(ContextObservation.id).where(ContextObservation.operation_id == Operation.id).exists()))
    first = await db.scalar(select(func.min(ContextObservation.created_at)))
    return {"scope": "main_session_open", "since": first, "methods": methods,
            "dispatched_operations_without_observations": without}
