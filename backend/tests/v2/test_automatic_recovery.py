import asyncio
from datetime import timedelta
import pytest
from sqlalchemy import select,func
from app.core.time import utc_now
from app.design.models import Operation,Wallet,RecoveryCheck,AdminAction
from app.admin.v2_controls import set_control
from app.admin.v2_service import Command,submit_command
from app.operations.automatic_recovery import claim_check,run_automatic_recovery,summary
from app.operations.worker import LocalWorker
from .test_operations import factory
from .test_migrations import migration_database
from .test_reconciliation import unknown
from .test_admin_recovery import admin,configure

async def test_automatic_recovery_uses_original_history_once(factory,tmp_path):
    worker,_,owner,_,opid,_,calls=await unknown(factory,tmp_path)
    identity=await run_automatic_recovery(worker)
    assert identity and calls==["thread/read"]
    assert await run_automatic_recovery(worker) is None
    async with factory() as db:
        op=await db.get(Operation,opid);assert op.state=="succeeded"
        wallet=await db.get(Wallet,owner);assert (wallet.balance,wallet.reserved)==(4,0)
        row=await db.get(RecoveryCheck,identity)
        assert row.attempt==1 and row.status=="completed" and row.next_check_at is None
        assert row.operation_state=="succeeded"
        assert (await summary(db,op,worker.settings))["state"]=="idle"

async def test_unknown_history_backs_off_then_stops_without_releasing_or_dispatching(factory,tmp_path,monkeypatch):
    worker,_,owner,_,opid,response,calls=await unknown(factory,tmp_path)
    response["thread"]["turns"][0]["status"]="inProgress"
    import app.operations.automatic_recovery as module
    now=utc_now();monkeypatch.setattr(module,"utc_now",lambda:now)
    for attempt in range(1,5):
        identity=await run_automatic_recovery(worker);assert identity
        assert await run_automatic_recovery(worker) is None
        async with factory() as db:
            row=await db.get(RecoveryCheck,identity)
            assert row.attempt==attempt and row.status=="unresolved"
            if attempt<4:
                assert row.next_check_at==now+timedelta(seconds=module.delay(attempt))
                now=row.next_check_at+timedelta(seconds=1)
    assert calls==["thread/read"]*4
    now+=timedelta(hours=1)
    assert await run_automatic_recovery(worker) is None
    async with factory() as db:
        wallet=await db.get(Wallet,owner);assert (wallet.balance,wallet.reserved)==(4,0)
        op=await db.get(Operation,opid);assert op.state=="reconciling"
        assert (await summary(db,op,worker.settings))["state"]=="manual_required"

async def test_two_workers_do_not_duplicate_automatic_check(factory,tmp_path):
    worker,runtime,_,_,_,_,calls=await unknown(factory,tmp_path)
    other=LocalWorker(factory,worker.settings,runtime=runtime)
    results=await asyncio.gather(run_automatic_recovery(worker),run_automatic_recovery(other))
    assert sum(r is not None for r in results)==1 and calls==["thread/read"]
    async with factory() as db:assert await db.scalar(select(func.count()).select_from(RecoveryCheck))==1

async def test_crashed_check_waits_before_new_fenced_read(factory,tmp_path,monkeypatch):
    worker,_,_,_,_,_,calls=await unknown(factory,tmp_path)
    claim=await claim_check(worker);assert claim
    assert await run_automatic_recovery(worker) is None and calls==[]
    import app.operations.automatic_recovery as module
    later=utc_now()+timedelta(seconds=301);monkeypatch.setattr(module,"utc_now",lambda:later)
    second=await run_automatic_recovery(worker);assert second and second!=claim[0]
    assert calls==["thread/read"]
    async with factory() as db:
        original=await db.get(RecoveryCheck,claim[0]);assert original.status=="unknown"
        assert original.error_code=="recovery_interrupted"
        assert (await db.get(RecoveryCheck,second)).attempt==2

async def test_live_lease_and_pending_manual_request_take_priority(factory,tmp_path,monkeypatch):
    worker,_,owner,_,opid,_,calls=await unknown(factory,tmp_path)
    async with factory.begin() as db:
        op=await db.get(Operation,opid);op.lease_expires_at=utc_now()+timedelta(minutes=1)
    assert await run_automatic_recovery(worker) is None
    async with factory.begin() as db:(await db.get(Operation,opid)).lease_expires_at=None
    actor=await admin(factory);configure(monkeypatch,owner)
    async with factory.begin() as db:
        await submit_command(db,actor,Command(action="reconcile",target_id=opid,reason="QA manual priority",client_request_id="manual-first"))
    assert await run_automatic_recovery(worker) is None and calls==[]
    async with factory() as db:assert await db.scalar(select(func.count()).select_from(RecoveryCheck))==0

async def test_quarantine_skips_but_generation_pause_allows_history_read(factory,tmp_path):
    worker,_,owner,_,_,_,calls=await unknown(factory,tmp_path)
    async with factory.begin() as db:
        await set_control(db,"owner:"+owner,True);await set_control(db,"global",True)
    assert await run_automatic_recovery(worker) is None and calls==[]
    async with factory.begin() as db:await set_control(db,"owner:"+owner,False)
    assert await run_automatic_recovery(worker) and calls==["thread/read"]

async def test_wrong_runtime_version_stops_for_manual_attention(factory,tmp_path):
    worker,runtime,owner,_,opid,_,calls=await unknown(factory,tmp_path)
    runtime.version="different-version"
    identity=await run_automatic_recovery(worker);assert identity
    assert await run_automatic_recovery(worker) is None and calls==[]
    async with factory() as db:
        row=await db.get(RecoveryCheck,identity);assert row.status=="blocked" and row.next_check_at is None
        op=await db.get(Operation,opid);assert op.state=="reconciling"
        assert (await db.get(Wallet,owner)).balance==4

async def test_disabled_aged_and_foreign_operations_are_not_claimed(factory,tmp_path,monkeypatch):
    worker,runtime,owner,_,opid,_,calls=await unknown(factory,tmp_path)
    worker.settings.v2_auto_reconcile_enabled=False
    assert await run_automatic_recovery(worker) is None
    worker.settings.v2_auto_reconcile_enabled=True
    other=LocalWorker(factory,worker.settings.model_copy(update={"v2_local_user_id":"unrelated"}),runtime=runtime)
    assert await run_automatic_recovery(other) is None
    import app.operations.automatic_recovery as module
    later=utc_now()+timedelta(days=2);monkeypatch.setattr(module,"utc_now",lambda:later)
    assert await run_automatic_recovery(worker) is None and calls==[]
    async with factory() as db:
        assert (await summary(db,await db.get(Operation,opid),worker.settings))["state"]=="manual_required"

async def test_final_crashed_attempt_becomes_manual_without_an_extra_read(factory,tmp_path,monkeypatch):
    worker,_,_,_,opid,_,calls=await unknown(factory,tmp_path)
    worker.settings.v2_auto_reconcile_max_attempts=1
    claimed=await claim_check(worker)
    import app.operations.automatic_recovery as module
    later=utc_now()+timedelta(seconds=301);monkeypatch.setattr(module,"utc_now",lambda:later)
    assert await run_automatic_recovery(worker) is None and calls==[]
    async with factory() as db:
        row=await db.get(RecoveryCheck,claimed[0]);assert row.status=="unknown"
        assert row.next_check_at is None
        assert (await summary(db,await db.get(Operation,opid),worker.settings))["state"]=="manual_required"
