import asyncio
from datetime import timedelta
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from app.auth.models import UserRole
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import User
from app.design.models import AdminAction,Operation,Wallet,Revision,LedgerEntry
from app.design.service import ApplicationError
from app.admin.v2_service import Command,submit_command,run_recovery
from app.operations.leases import claim,record_dispatch_intent
from .test_api import api
from .test_operations import factory,setup,quoted,submit_request,request
from .test_migrations import migration_database
from .test_worker import prepared
from .test_reconciliation import unknown

async def admin(factory):
    owner,_,_=await setup(factory)
    async with factory.begin() as db:(await db.get(User,owner)).role=UserRole.admin
    return owner

def configure(monkeypatch,owner):
    monkeypatch.setenv("V2_RUNTIME_MODE","local");monkeypatch.setenv("V2_LOCAL_USER_ID",owner);get_settings.cache_clear()

async def send(factory,actor,action,target="global",key=None,**extra):
    payload=Command(client_request_id=key or "request-"+action,action=action,target_id=target,reason="QA recovery request",**extra)
    async with factory.begin() as db:
        row=await submit_command(db,actor,payload)
        return row.id

async def test_admin_api_rejects_ordinary_and_unauthenticated_users(api):
    client,_,_,_,_=api
    assert (await client.get("/api/admin/v2/overview")).status_code==403
    assert (await client.post("/api/admin/v2/actions",json={"client_request_id":"request-pause","action":"pause","reason":"QA pause"})).status_code==403
    client.cookies.clear()
    assert (await client.get("/api/admin/v2/operations")).status_code==401

async def test_pause_replay_conflict_and_no_wallet_or_dispatch_side_effects(api,tmp_path):
    client,factory,owner,_,branch=api
    payload=await quoted(factory,owner,branch)
    opid=await submit_request(factory,owner,branch,payload)
    async with factory.begin() as db:(await db.get(User,owner)).role=UserRole.admin
    command={"client_request_id":"pause-request-1","action":"pause","reason":"QA temporary pause"}
    first=await client.post("/api/admin/v2/actions",json=command)
    assert first.status_code==200,first.text
    duplicate=await client.post("/api/admin/v2/actions",json=command)
    assert duplicate.json()["id"]==first.json()["id"] and duplicate.json()["replay_count"]==1
    conflict=await client.post("/api/admin/v2/actions",json={**command,"action":"resume"})
    assert conflict.status_code==409
    async with factory.begin() as db:
        assert await claim(db,"paused-worker",owner_id=owner) is None
        w=await db.get(Wallet,owner);assert (w.balance,w.reserved)==(4,0)
        assert (await db.get(Operation,opid)).state=="queued"
    assert await submit_request(factory,owner,branch,payload)==opid
    with pytest.raises(ApplicationError,match="generation_paused"):
        await submit_request(factory,owner,branch,payload.model_copy(update={"client_request_id":"new-after-pause"}))
    output=(await client.get("/api/admin/v2/operations/"+opid)).json()
    assert output["upstream_cost"]=={"status":"unknown","amount":None}
    for name in ("upstream_thread_id","staged_outputs","input_snapshot","content_parts","runtime_identity"):
        assert name not in output
    assert (await client.post("/api/admin/v2/actions",json={**command,"client_request_id":"resume-request","action":"resume"})).status_code==200
    async with factory.begin() as db:assert await claim(db,"resumed-worker",owner_id=owner)
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:await db.execute(text("DELETE FROM v2_admin_actions"))
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:await db.execute(text("UPDATE v2_admin_actions SET reason='changed'"))

async def test_publish_recovery_is_durable_and_never_reopens_model(factory,tmp_path,monkeypatch):
    worker,runtime,owner,_,opid=await prepared(factory,tmp_path)
    configure(monkeypatch,owner);actor=await admin(factory)
    import app.operations.worker as module
    real=module.publish
    async def fail(*args,**kwargs):raise OSError("secret source prompt must not reach audit")
    monkeypatch.setattr(module,"publish",fail);await worker.run_once()
    monkeypatch.setattr(module,"publish",real)
    calls=len(runtime.calls)
    identity=await send(factory,actor,"retry_publish",opid)
    assert await send(factory,actor,"retry_publish",opid)==identity
    with pytest.raises(ApplicationError,match="recovery_already_requested"):
        await send(factory,actor,"retry_publish",opid,key="another-request")
    assert await run_recovery(worker)==identity
    assert await run_recovery(worker) is None
    assert len(runtime.calls)==calls
    assert await send(factory,actor,"retry_publish",opid)==identity
    async with factory() as db:
        row=await db.get(AdminAction,identity);assert row.status=="completed"
        assert row.result=={"operation_state":"succeeded"}
        w=await db.get(Wallet,owner);assert (w.balance,w.reserved)==(4,0)
        assert await db.scalar(select(func.count()).select_from(Revision))==1
        assert await db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind=="settle"))==1

async def test_refund_concurrency_caps_settlement_and_preserves_current_holds(factory,tmp_path,monkeypatch):
    worker,_,owner,branch,opid=await prepared(factory,tmp_path);await worker.run_once()
    actor=await admin(factory)
    async with factory() as db:
        op=await db.get(Operation,opid)
        from app.design.models import Branch
        current=await db.get(Branch,branch)
        payload=request(client_request_id="pending-next",base_revision_id=current.current_revision_id,expected_branch_version=current.state_version)
    await submit_request(factory,owner,branch,await quoted(factory,owner,branch,payload))
    outcomes=await asyncio.gather(send(factory,actor,"refund",opid,key="refund-once",units=1),
        send(factory,actor,"refund",opid,key="refund-once",units=1))
    assert outcomes[0]==outcomes[1]
    with pytest.raises(ApplicationError,match="refund_exceeds_settlement"):
        await send(factory,actor,"refund",opid,key="refund-again",units=1)
    async with factory() as db:
        w=await db.get(Wallet,owner);assert (w.balance,w.reserved)==(4,0)
        assert await db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind=="refund"))==1
        assert await db.scalar(select(func.count()).select_from(AdminAction).where(AdminAction.action=="refund"))==1

@pytest.mark.parametrize("known",[True,False])
async def test_reconciliation_reads_original_history_only(factory,tmp_path,monkeypatch,known):
    worker,_,owner,_,opid,response,calls=await unknown(factory,tmp_path)
    configure(monkeypatch,owner);actor=await admin(factory)
    if not known:response["thread"]["turns"]=[]
    identity=await send(factory,actor,"reconcile",opid)
    assert await run_recovery(worker)==identity and calls==["thread/read"]
    assert await send(factory,actor,"reconcile",opid)==identity
    assert await run_recovery(worker) is None and calls==["thread/read"]
    async with factory() as db:
        row=await db.get(AdminAction,identity)
        assert row.status==("completed" if known else "unresolved")
        w=await db.get(Wallet,owner);assert (w.balance,w.reserved)==(4,0)

async def test_quarantine_rechecked_before_dispatch_and_recovery(factory,tmp_path,monkeypatch):
    worker,runtime,owner,_,opid=await prepared(factory,tmp_path)
    configure(monkeypatch,owner);actor=await admin(factory)
    async with factory.begin() as db:
        op=await claim(db,"claimed",owner_id=owner);token=op.fencing_token
    await send(factory,actor,"quarantine",owner)
    with pytest.raises(ApplicationError,match="runtime_quarantined"):
        async with factory.begin() as db:await record_dispatch_intent(db,opid,"claimed",token,"thread")
    with pytest.raises(ApplicationError,match="runtime_quarantined"):await worker.reconcile(opid)
    assert runtime.calls==[]
    async with factory() as db:
        w=await db.get(Wallet,owner);assert (w.balance,w.reserved)==(4,0)
        assert (await db.get(Operation,opid)).dispatch_intent_at is None

async def test_interrupted_request_is_not_automatically_reexecuted(factory,tmp_path,monkeypatch):
    worker,_,owner,_,opid,_,calls=await unknown(factory,tmp_path)
    configure(monkeypatch,owner);actor=await admin(factory)
    identity=await send(factory,actor,"reconcile",opid)
    async with factory.begin() as db:
        row=await db.get(AdminAction,identity);row.status="running";row.started_at=utc_now()-timedelta(minutes=11)
    assert await run_recovery(worker) is None and calls==[]
    async with factory() as db:
        row=await db.get(AdminAction,identity);assert row.status=="unknown"
        assert row.error_code=="recovery_interrupted"
    assert await send(factory,actor,"reconcile",opid)==identity
    assert await run_recovery(worker) is None

async def test_recovery_checks_admin_role_again_at_execution(factory,tmp_path,monkeypatch):
    worker,_,owner,_,opid,_,calls=await unknown(factory,tmp_path)
    configure(monkeypatch,owner);actor=await admin(factory)
    identity=await send(factory,actor,"reconcile",opid)
    async with factory.begin() as db:(await db.get(User,actor)).is_banned=True
    assert await run_recovery(worker)==identity and calls==[]
    async with factory() as db:
        row=await db.get(AdminAction,identity);assert row.status=="failed" and row.error_code=="admin_required"
