"""Audited admin commands; request replay returns the same receipt, never a second effect."""
from app.codex_runtime.selection import execution_owner, owner_clause, runtime_allowed
from datetime import timedelta
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text, func
from app.auth.models import UserRole
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import User
from app.design.models import AdminAction, Operation, LedgerEntry
from app.design.service import ApplicationError, digest
from app.operations.service import TERMINAL
from app.quota.adjustments import refund
from app.admin.v2_controls import gate, set_control

class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    client_request_id: str = Field(min_length=8, max_length=100)
    action: Literal["pause", "resume", "quarantine", "unquarantine", "refund", "reconcile", "retry_publish"]
    target_id: str = Field(default="global", min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=500)
    units: int | None = Field(default=None, strict=True, ge=1, le=100000)
    @model_validator(mode="after")
    def valid_target(self):
        if self.action in {"pause","resume"} and self.target_id != "global":
            raise ValueError("Global control requires global target")
        if (self.action == "refund") != (self.units is not None):
            raise ValueError("Units are required only for a refund")
        return self

def receipt(row):
    return {key:getattr(row,key) for key in ("id","actor_id","client_request_id","action","target_id",
        "reason","units","status","result","error_code","replay_count","created_at","started_at","completed_at")}

async def submit_command(db, actor, payload):
    current = await db.get(User, actor)
    if current is None or current.is_banned or current.role != UserRole.admin:
        raise ApplicationError("admin_required")
    # A request identity is scoped to the administrator, not a target that could be changed on retry.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:value, 0))"),
                     {"value":"v2-admin:"+actor+":"+payload.client_request_id})
    fingerprint = digest(payload.model_dump(mode="json"))
    old = await db.scalar(select(AdminAction).where(AdminAction.actor_id==actor,
        AdminAction.client_request_id==payload.client_request_id))
    if old:
        if old.request_digest != fingerprint:
            raise ApplicationError("idempotency_conflict")
        old.replay_count += 1
        await db.flush()
        return old
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:value, 0))"),
                     {"value":"v2-admin-target:"+payload.target_id})
    owner = None
    if payload.action in {"quarantine","unquarantine"}:
        if await db.get(User,payload.target_id) is None:
            raise ApplicationError("not_found")
        owner = payload.target_id
    elif payload.action not in {"pause","resume"}:
        operation = await db.get(Operation,payload.target_id)
        if operation is None:
            raise ApplicationError("not_found")
        owner = operation.owner_id
        if payload.action in {"reconcile","retry_publish"}:
            # Recovery remains routed to the explicitly provisioned account Worker.
            settings=get_settings()
            if not runtime_allowed(settings, owner):
                raise ApplicationError("runtime_not_configured")
            if payload.action=="reconcile":
                await gate(db,owner,recovery=True)
            if operation.lease_expires_at and operation.lease_expires_at > utc_now():
                raise ApplicationError("operation_busy")
            valid = (operation.state in {"reconciling","cancelling"} and operation.upstream_thread_id
                     if payload.action=="reconcile" else operation.state=="publishing" and
                     operation.upstream_status is not None and not operation.cancel_requested and operation.staged_outputs)
            if not valid:
                raise ApplicationError("recovery_unavailable")
            active=await db.scalar(select(AdminAction.id).where(AdminAction.target_id==operation.id,
                AdminAction.status.in_(["pending","running"])).limit(1))
            if active:
                raise ApplicationError("recovery_already_requested")
    row=AdminAction(actor_id=actor,client_request_id=payload.client_request_id,request_digest=fingerprint,
        action=payload.action,target_id=payload.target_id,owner_id=owner,reason=payload.reason,units=payload.units,
        status="pending" if payload.action in {"reconcile","retry_publish"} else "completed")
    db.add(row);await db.flush()
    if payload.action in {"pause","resume","quarantine","unquarantine"}:
        await set_control(db,"global" if owner is None else "owner:"+owner,
                          payload.action in {"pause","quarantine"})
        row.result={"blocked":payload.action in {"pause","quarantine"}}
    elif payload.action=="refund":
        entry=await refund(db,payload.target_id,units=payload.units,key=row.id,actor_id=actor,reason=payload.reason)
        row.result={"ledger_id":entry.id,"units":entry.balance_delta}
    if row.status=="completed":
        row.completed_at=utc_now()
    await db.flush()
    return row

async def run_recovery(worker):
    """Claim once; stale requests are visible as unknown, never automatically re-run."""
    async with worker.factory.begin() as db:
        stale=list((await db.scalars(select(AdminAction).where(owner_clause(AdminAction.owner_id,execution_owner(worker)),
            AdminAction.status=="running",AdminAction.started_at<utc_now()-timedelta(minutes=10))
            .with_for_update(skip_locked=True))).all())
        for action in stale:
            op=await db.get(Operation,action.target_id)
            if op and op.lease_expires_at and op.lease_expires_at>utc_now():
                continue
            action.status="unknown";action.error_code="recovery_interrupted";action.completed_at=utc_now()
        row=await db.scalar(select(AdminAction).where(AdminAction.status=="pending",
            owner_clause(AdminAction.owner_id,execution_owner(worker))).order_by(AdminAction.created_at,AdminAction.id)
            .limit(1).with_for_update(skip_locked=True))
        if row is None:
            return None
        actor=await db.get(User,row.actor_id)
        if actor is None or actor.is_banned or actor.role!=UserRole.admin:
            row.status="failed";row.error_code="admin_required";row.completed_at=utc_now()
            return row.id
        row.status="running";row.started_at=utc_now()
        identity,action,target=row.id,row.action,row.target_id
    status,result,error="failed",{},None
    try:
        async with worker.factory() as db:
            op=await db.get(Operation,target)
            finished=op is not None and op.state in TERMINAL
        if finished:
            result={"operation_state":op.state};status="completed"
        else:
            state=await (worker.reconcile(target) if action=="reconcile" else worker.retry_publish(target))
            result={"operation_state":state}
            status="completed" if state in TERMINAL else "unresolved"
    except ApplicationError as exc:
        # Only application codes, never arbitrary upstream exception strings.
        error=exc.code if exc.code in {"operation_busy","runtime_quarantined","reconciliation_unavailable",
            "publication_retry_unavailable","runtime_binding_unavailable","invalid_staging_descriptor","not_found"} else "recovery_failed"
    except Exception:
        error="recovery_failed"
    async with worker.factory.begin() as db:
        row=await db.scalar(select(AdminAction).where(AdminAction.id==identity).with_for_update())
        if row.status=="running":
            row.status=status;row.result=result;row.error_code=error;row.completed_at=utc_now()
    return identity
