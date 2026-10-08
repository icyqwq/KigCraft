"""Operator-assisted account closure following a verified owner deletion request.

This closes access and queues existing data-retention workflows. It does not
claim that native files, legacy originals or backups have already been erased.
"""
import argparse
import asyncio
from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.models import UserRole
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import AuthLoginFailure, AuthSession, EmailVerificationCode, GenerationJob, User
from app.db.v2_schema import require_v2_schema
from app.design.models import AccountClosure, AdminAction, Design, EditorDraft, Operation, ViewSet, Wallet
from app.maintenance.orphan_assets import CleanupError, database_identity, digest
from app.maintenance.remote_assets import paused
from app.maintenance.provider_retirement import settled_legacy_jobs
from app.maintenance.runtime_retention import administrator, identity
from app.maintenance.upload_retention import lock_registry
from app.operations.service import TERMINAL

REQUEST = "request_account_closure"
CANCEL = "cancel_account_closure"
CLOSE = "close_account"
SCOPE = "v2_account_closure"


async def receipt(db, actor, reason, action, request, result, now):
    # A savepoint has its own xmin. Bind the root transaction explicitly so
    # releasing that savepoint is not mistaken for committing the deletion intent.
    if action == REQUEST:
        result = {**result, "request_transaction": {
            "database": await database_identity(db), "id": await db.scalar(text("SELECT txid_current()"))}}
    # Request identity is immutable from INSERT, including the target account.
    row = AdminAction(id=str(uuid4()), actor_id=actor, client_request_id=request,
        request_digest=request, action=action, target_id=result["owner_id"], owner_id=result["owner_id"],
        reason=reason.strip(), status="completed", result=result,
        created_at=now, started_at=now, completed_at=now)
    db.add(row)
    await db.flush()
    return {"receipt_id":row.id, **result}


async def guard(db, settings, stopped):
    if not settings.v2_account_closure_enabled: raise CleanupError("account_closure_disabled")
    await lock_registry(db)
    await paused(db, settings, stopped)
    if await db.scalar(select(Operation.id).where(or_(
        Operation.state.not_in(TERMINAL), Operation.reservation_closed.is_(False),
        and_(Operation.dispatch_intent_at.is_not(None), or_(Operation.upstream_status.is_(None),
            Operation.upstream_status.not_in(["completed","failed","interrupted"]))),
        Operation.context_allocation["state"].as_string().in_(["sent","unknown"]))).limit(1)):
        raise CleanupError("operation_unconfirmed")
    if await db.scalar(select(ViewSet.id).where(or_(ViewSet.held_units != 0,
        ViewSet.state.not_in(["succeeded","partial","cancelled","failed"]))).limit(1)):
        raise CleanupError("viewset_unconfirmed")


async def target(db, owner):
    if not identity(owner): raise CleanupError("invalid_account_id")
    user = await db.get(User, owner)
    if user is None: raise CleanupError("account_not_found")
    if user.role == UserRole.admin: raise CleanupError("demote_admin_before_account_closure")
    if await db.scalar(select(GenerationJob.id).where(GenerationJob.user_id == owner,
        ~settled_legacy_jobs()).limit(1)):
        raise CleanupError("legacy_operation_unconfirmed")
    wallet = await db.get(Wallet, owner)
    if wallet is not None and wallet.reserved != 0: raise CleanupError("account_reservation_unconfirmed")
    return user


def state(row):
    if row is None: return None
    return {name: (value.isoformat() if hasattr(value, "isoformat") else value)
        for name in ("request_id","state","requested_at","delete_after","completed_at","cancelled_at","policy_days")
        for value in [getattr(row,name)]}


async def inspect_locked(db, settings, owner):
    user = await target(db,owner)
    row = await db.get(AccountClosure,owner)
    counts = {name: await db.scalar(select(func.count()).select_from(model).where(column == owner))
        for name,model,column in (("designs",Design,Design.owner_id),("drafts",EditorDraft,EditorDraft.owner_id),
                                 ("sessions",AuthSession,AuthSession.user_id))}
    report = {"format":1,"scope":SCOPE,"database":await database_identity(db),"owner_id":owner,
        "policy_days":settings.v2_account_closure_grace_days,"closure":state(row),"counts":counts,
        "identity_digest":digest({"email":user.email,"updated":user.updated_at.isoformat(),"banned":user.is_banned})}
    report["digest"] = digest(report)
    return report


async def inspect_account(db,settings,owner,*,writers_stopped=False):
    await guard(db,settings,writers_stopped)
    return await inspect_locked(db,settings,owner)


async def prior(db,actor,request):
    row = await db.scalar(select(AdminAction).where(AdminAction.actor_id==actor,AdminAction.client_request_id==request))
    return {"receipt_id":row.id,**row.result} if row else None


async def request_closure(db,settings,owner,expected_digest,actor,reason,*,writers_stopped=False,now=None):
    await guard(db,settings,writers_stopped)
    await administrator(db,actor,reason)
    if owner == actor: raise CleanupError("self_account_closure_forbidden")
    request = digest({"action":REQUEST,"owner":owner,"digest":expected_digest,"reason":reason.strip()})
    replay = await prior(db,actor,request)
    if replay: return replay
    report = await inspect_locked(db,settings,owner)
    if report["digest"] != expected_digest: raise CleanupError("account_inventory_changed")
    user = await target(db,owner)
    row = await db.get(AccountClosure,owner)
    if row is not None and row.state != "cancelled": raise CleanupError("account_closure_already_requested")
    now = now or utc_now()
    async with db.begin_nested():
        banned = user.is_banned
        user.is_banned = True
        user.generation_quality_improvement_consent = False
        user.generation_quality_improvement_consent_updated_at = now
        await db.execute(update(AuthSession).where(AuthSession.user_id==owner,AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        await db.flush()
        if row is None: row=AccountClosure(owner_id=owner); db.add(row)
        row.request_id=str(uuid4()); row.state="pending"; row.requested_at=now
        row.delete_after=now+timedelta(days=settings.v2_account_closure_grace_days)
        row.policy_days=settings.v2_account_closure_grace_days; row.previously_banned=banned
        row.completed_at=None; row.cancelled_at=None
        await db.flush()
        return await receipt(db,actor,reason,REQUEST,request,{"owner_id":owner,**state(row),
            "identity_erased":False,"data_erased":False,"backups_erased":False},now)


async def cancel_closure(db,settings,owner,request_id,actor,reason,*,writers_stopped=False,now=None):
    await guard(db,settings,writers_stopped)
    await administrator(db,actor,reason)
    user = await target(db,owner)
    row = await db.get(AccountClosure,owner)
    if row is None or row.request_id != request_id: raise CleanupError("account_closure_request_mismatch")
    request=digest({"action":CANCEL,"owner":owner,"request_id":request_id,"reason":reason.strip()})
    replay=await prior(db,actor,request)
    if replay:return replay
    now=now or utc_now()
    if row.state != "pending" or now >= row.delete_after: raise CleanupError("account_closure_not_reversible")
    async with db.begin_nested():
        row.state="cancelled";row.cancelled_at=now
        await db.flush()
        user.is_banned=row.previously_banned
        await db.flush()
        return await receipt(db,actor,reason,CANCEL,request,{"owner_id":owner,**state(row),
            "sessions_restored":False,"consent_restored":False},now)


async def close_account(db,settings,owner,request_id,actor,reason,*,writers_stopped=False,now=None):
    await guard(db,settings,writers_stopped)
    await administrator(db,actor,reason)
    user = await target(db,owner)
    row=await db.get(AccountClosure,owner)
    if row is None or row.request_id != request_id: raise CleanupError("account_closure_request_mismatch")
    request=digest({"action":CLOSE,"owner":owner,"request_id":request_id,"reason":reason.strip()})
    replay=await prior(db,actor,request)
    if replay:return replay
    if row.state != "pending":raise CleanupError("account_closure_not_pending")
    proof = await db.scalar(select(AdminAction).where(AdminAction.action == REQUEST,
        AdminAction.owner_id == owner, AdminAction.status == "completed",
        AdminAction.result["request_id"].as_string() == request_id))
    if proof is None or not isinstance(proof.result.get("request_transaction"), dict):
        raise CleanupError("account_closure_request_proof_missing")
    current_transaction = {"database": await database_identity(db), "id": await db.scalar(text("SELECT txid_current()"))}
    if proof.result["request_transaction"] == current_transaction:
        raise CleanupError("account_closure_commit_required")
    now=now or utc_now()
    if now < max(row.delete_after,row.requested_at+timedelta(days=settings.v2_account_closure_grace_days)):
        raise CleanupError("account_closure_grace_period")
    async with db.begin_nested():
        old_email=user.email
        row.state="closed";row.completed_at=now
        await db.flush()
        user.email="closed+"+owner+"@kigcraft.invalid"
        user.password_hash=None;user.email_verified_at=None;user.last_login_at=None
        user.is_banned=True;user.generation_quality_improvement_consent=False
        user.generation_quality_improvement_consent_updated_at=now
        await db.execute(delete(AuthSession).where(AuthSession.user_id==owner))
        await db.execute(delete(EmailVerificationCode).where(func.lower(EmailVerificationCode.email)==old_email.lower()))
        await db.execute(delete(AuthLoginFailure).where(func.lower(AuthLoginFailure.email)==old_email.lower()))
        # Account deletion explicitly ends online restoration rights after its grace.
        # A timestamp strictly after deleted_at also preserves the existing constraint.
        designs=list(await db.scalars(select(Design).where(Design.owner_id==owner)))
        for design in designs:
            if design.content_purged_at is not None:continue
            design.deleted_at=design.deleted_at or now-timedelta(microseconds=1)
            design.trash_restore_until=max(now,design.deleted_at+timedelta(microseconds=1))
            design.metadata_version+=1
        await db.execute(update(EditorDraft).where(EditorDraft.owner_id==owner).values(
            deleted_at=func.coalesce(EditorDraft.deleted_at,now),document={},device_id=""))
        await db.flush()
        return await receipt(db,actor,reason,CLOSE,request,{"owner_id":owner,**state(row),
            "identity_erased":True,"designs_queued":len(designs),"data_erased":False,"backups_erased":False},now)


async def run(settings,args):
    engine=create_async_engine(settings.database_url,hide_parameters=True)
    try:
        await require_v2_schema(settings=settings,engine=engine)
        async with async_sessionmaker(engine,expire_on_commit=False).begin() as db:
            options={"writers_stopped":args.writers_stopped}
            if args.command=="inspect":return await inspect_account(db,settings,args.owner_id,**options)
            if args.command=="request":return await request_closure(db,settings,args.owner_id,args.expected_digest,args.actor_id,args.reason,**options)
            function=cancel_closure if args.command=="cancel" else close_account
            return await function(db,settings,args.owner_id,args.request_id,args.actor_id,args.reason,**options)
    finally:await engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["inspect","request","cancel","close"])
    parser.add_argument("--env-file",type=Path,required=True)
    parser.add_argument("--owner-id",required=True)
    parser.add_argument("--writers-stopped",action="store_true")
    for name in ("expected-digest","request-id","actor-id","reason"):parser.add_argument("--"+name)
    args=parser.parse_args()
    try:
        if not args.env_file.is_file():raise CleanupError("configuration_file_missing")
        result=asyncio.run(run(Settings(_env_file=args.env_file),args))
    except Exception as exc:
        print(json.dumps({"status":"refused","code":str(exc) if isinstance(exc,CleanupError) else "maintenance_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result,ensure_ascii=True))


if __name__=="__main__":main()
