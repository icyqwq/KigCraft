"""Bounded durable read-only reconciliation. Never schedules a replacement model turn."""
from app.codex_runtime.selection import execution_owner, owner_clause, SHARED_OWNER
from datetime import timedelta
from sqlalchemy import select,func,or_,exists
from app.admin.v2_controls import gate
from app.core.time import utc_now
from app.design.models import AdminAction,Design,Operation,RecoveryCheck
from app.design.service import ApplicationError
from app.operations.service import TERMINAL

BACKOFF_SECONDS=(30,120,600)
CLAIM_SECONDS=300

def delay(attempt):
    return BACKOFF_SECONDS[min(attempt-1,len(BACKOFF_SECONDS)-1)]

async def claim_check(worker):
    settings=worker.settings
    if not settings.v2_auto_reconcile_enabled or not execution_owner(worker):
        return None
    now=utc_now()
    async with worker.factory.begin() as db:
        try:
            await gate(db,None if execution_owner(worker) == SHARED_OWNER else execution_owner(worker),recovery=True)
        except ApplicationError as exc:
            if exc.code=="runtime_quarantined":
                return None
            raise
        stale=list((await db.scalars(select(RecoveryCheck).join(Operation,Operation.id==RecoveryCheck.operation_id).where(
            owner_clause(Operation.owner_id,execution_owner(worker)),RecoveryCheck.status=="running",RecoveryCheck.next_check_at<=now,
            or_(Operation.lease_expires_at.is_(None),Operation.lease_expires_at<=now)
        ).with_for_update(of=RecoveryCheck,skip_locked=True))).all())
        for record in stale:
            record.status="unknown";record.error_code="recovery_interrupted";record.completed_at=now
            if record.attempt>=settings.v2_auto_reconcile_max_attempts:
                record.next_check_at=None
        await db.flush()
        count=select(func.count()).select_from(RecoveryCheck).where(
            RecoveryCheck.operation_id==Operation.id).correlate(Operation).scalar_subquery()
        due=select(RecoveryCheck.next_check_at).where(RecoveryCheck.operation_id==Operation.id).order_by(
            RecoveryCheck.attempt.desc()).limit(1).correlate(Operation).scalar_subquery()
        manual=exists(select(AdminAction.id).where(AdminAction.target_id==Operation.id,
            AdminAction.status.in_(["pending","running"]))).correlate(Operation)
        candidate=await db.scalar(select(Operation.id).join(Design,Design.id==Operation.design_id).where(
            owner_clause(Operation.owner_id,execution_owner(worker)),
            Operation.state.in_(["reconciling","cancelling"]),
            Operation.upstream_thread_id.is_not(None),
            Operation.dispatch_intent_at>=now-timedelta(seconds=settings.v2_auto_reconcile_window_seconds),
            or_(Operation.lease_expires_at.is_(None),Operation.lease_expires_at<=now),
            count<settings.v2_auto_reconcile_max_attempts,
            or_(count==0,due<=now),~manual,
        ).order_by(Operation.updated_at,Operation.id).limit(1).with_for_update(of=Design,skip_locked=True))
        if candidate is None:
            return None
        operation=await db.scalar(select(Operation).where(Operation.id==candidate).with_for_update())
        previous=await db.scalar(select(RecoveryCheck).where(RecoveryCheck.operation_id==candidate)
            .order_by(RecoveryCheck.attempt.desc()).limit(1))
        if previous and previous.status=="running":
            # The original operation's lease is expired. A new read takes a new fencing token.
            previous.status="unknown";previous.error_code="recovery_interrupted";previous.completed_at=now
        row=RecoveryCheck(operation_id=candidate,attempt=1 if previous is None else previous.attempt+1,
            worker_id=worker.worker_id,started_at=now,next_check_at=now+timedelta(seconds=CLAIM_SECONDS))
        db.add(row);await db.flush()
        return row.id,candidate,row.attempt

async def run_automatic_recovery(worker):
    claimed=await claim_check(worker)
    if claimed is None:
        return None
    identity,operation_id,attempt=claimed
    status,error,state="unresolved",None,None
    try:
        state=await worker.reconcile(operation_id)
        status="completed" if state in TERMINAL or state=="publishing" else "unresolved"
    except ApplicationError as exc:
        error=exc.code if exc.code in {"operation_busy","runtime_quarantined","runtime_binding_unavailable",
            "reconciliation_unavailable","not_found","stale_lease"} else "recovery_failed"
        status="blocked" if error in {"runtime_binding_unavailable","not_found"} else "unresolved"
    except Exception:
        # Unknown transport/storage errors never imply failure of the model turn.
        error="recovery_failed"
    async with worker.factory.begin() as db:
        row=await db.scalar(select(RecoveryCheck).where(RecoveryCheck.id==identity).with_for_update())
        if row.status!="running":
            return identity
        operation=await db.get(Operation,operation_id)
        if operation is not None:
            state=operation.state
            if state in TERMINAL or state=="publishing":
                status="completed"
        row.status=status;row.operation_state=state;row.error_code=error;row.completed_at=utc_now()
        exhausted=(attempt>=worker.settings.v2_auto_reconcile_max_attempts or operation is None
            or operation.dispatch_intent_at is None or operation.dispatch_intent_at<utc_now()-timedelta(
                seconds=worker.settings.v2_auto_reconcile_window_seconds))
        row.next_check_at=(utc_now()+timedelta(seconds=delay(attempt))
            if status=="unresolved" and not exhausted else None)
    return identity

async def summary(db,operation,settings):
    last=await db.scalar(select(RecoveryCheck).where(RecoveryCheck.operation_id==operation.id)
        .order_by(RecoveryCheck.attempt.desc()).limit(1))
    checks=0 if last is None else last.attempt
    state="idle"
    if operation.state in {"reconciling","cancelling"}:
        from app.design.models import RuntimeControl
        control=await db.get(RuntimeControl,"owner:"+operation.owner_id)
        if control and control.blocked:state="quarantined"
        elif not settings.v2_auto_reconcile_enabled:state="disabled"
        elif (not operation.upstream_thread_id or not operation.dispatch_intent_at or
            operation.dispatch_intent_at<utc_now()-timedelta(seconds=settings.v2_auto_reconcile_window_seconds) or
            checks>=settings.v2_auto_reconcile_max_attempts and last.status!="running" or
            last and last.next_check_at is None):state="manual_required"
        elif last and last.status=="running":state="running"
        else:state="scheduled"
    return {"state":state,"attempts":checks,"max_attempts":settings.v2_auto_reconcile_max_attempts,
        "next_check_at":last.next_check_at if last and state in {"scheduled","running"} else None,
        "last_result":last.status if last else None}
