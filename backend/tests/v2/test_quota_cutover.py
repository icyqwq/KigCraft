import asyncio
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.auth.models import UserRole
from app.core.time import utc_now
from app.db.models import User, Project, GenerationJob, JobStatus, UserGenerationQuotaWindow
from app.design.models import LedgerEntry, Operation, Wallet
from app.design.service import ApplicationError
from app.generation.quota import consume_generation_quota, current_generation_quota_window, get_generation_quota_state, refund_generation_quota
from app.operations.cancel import cancel
from app.operations.publish import publish
from app.quota.adjustments import adjust, refund
from app.quota.cutover import freeze, cut_over
from app.quota.policy import top_up
from app.quota.wallet import locked_wallet
from .test_operations import factory, setup, quoted, submit_request, running, staged
from .test_migrations import migration_database, create_legacy


async def actors(factory, balance=7):
    start, _ = current_generation_quota_window()
    async with factory.begin() as db:
        owner = User(email=f"{uuid4()}@example.com",daily_generation_balance=balance,daily_generation_limit_override=10)
        admin = User(email=f"{uuid4()}@example.com",role=UserRole.admin)
        db.add_all([owner,admin])
        await db.flush()
        db.add(UserGenerationQuotaWindow(user_id=owner.id,window_start_at=start,used_count=max(0,10-balance)))
        return owner.id,admin.id

async def freeze_owner(factory, owner, admin):
    async with factory.begin() as db:
        await freeze(db,owner,actor_id=admin,reason="Isolated migration test")

async def migrate_owner(factory, owner, admin):
    async with factory.begin() as db:
        return await cut_over(db,owner,actor_id=admin,reason="Verified old jobs drained")

async def job(factory, owner, status="failed"):
    async with factory.begin() as db:
        project=Project(user_id=owner,name="Legacy")
        db.add(project)
        await db.flush()
        record=GenerationJob(project_id=project.id,user_id=owner,runtime_status=status,
            status=JobStatus.queued if status=="queued" else JobStatus.failed,
            prompt_payload={"quota_reservation":{"window_start_at":current_generation_quota_window()[0].isoformat()}})
        db.add(record)
        await db.flush()
        return record.id

async def test_cutover_preserves_opening_and_old_reader_uses_new_wallet(factory):
    owner,admin=await actors(factory)
    await freeze_owner(factory,owner,admin)
    first=await migrate_owner(factory,owner,admin)
    replay=await migrate_owner(factory,owner,admin)
    assert first.owner_id==replay.owner_id
    async with factory.begin() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved,wallet.write_owner)==(7,0,"v2")
        state=await get_generation_quota_state(db,await db.get(User,owner))
        assert state.remaining==7
        assert (await db.execute(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.owner_id==owner,LedgerEntry.kind=="opening"))).scalar_one()==1
    with pytest.raises(HTTPException) as exc:
        async with factory.begin() as db:
            await consume_generation_quota(db,await db.get(User,owner))
    assert exc.value.status_code==409

async def test_freeze_refuses_new_old_charge_and_cutover_waits_for_old_job(factory):
    owner,admin=await actors(factory)
    await job(factory,owner,"queued")
    await freeze_owner(factory,owner,admin)
    with pytest.raises(HTTPException):
        async with factory.begin() as db:
            await consume_generation_quota(db,await db.get(User,owner))
    with pytest.raises(ApplicationError,match="legacy_jobs_active"):
        await migrate_owner(factory,owner,admin)
    async with factory() as db:
        assert (await db.get(Wallet,owner)).write_owner=="frozen"
        assert (await db.get(User,owner)).daily_generation_balance==7

async def test_cutover_crash_rolls_back_opening_and_owner_together(factory):
    owner,admin=await actors(factory)
    await freeze_owner(factory,owner,admin)
    with pytest.raises(RuntimeError,match="injected"):
        async with factory.begin() as db:
            await cut_over(db,owner,actor_id=admin,reason="Crash test")
            raise RuntimeError("injected")
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.write_owner)==(0,"frozen")
        assert (await db.execute(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.owner_id==owner,LedgerEntry.kind=="opening"))).scalar_one()==0
    assert (await migrate_owner(factory,owner,admin)).balance==7

async def test_late_legacy_refund_credits_new_ledger_once(factory):
    owner,admin=await actors(factory)
    job_id=await job(factory,owner)
    await freeze_owner(factory,owner,admin)
    await migrate_owner(factory,owner,admin)
    for _ in range(2):
        async with factory.begin() as db:
            assert await refund_generation_quota(db,owner,current_generation_quota_window()[0],legacy_job_id=job_id)
    async with factory() as db:
        assert (await db.get(Wallet,owner)).balance==8
        assert (await db.get(User,owner)).daily_generation_balance==7
        assert (await db.execute(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.owner_id==owner,LedgerEntry.kind=="legacy_refund"))).scalar_one()==1

async def test_legacy_charge_lock_allows_repository_fk_insert_then_freeze_drains(factory):
    owner,admin=await actors(factory)
    async with factory.begin() as old:
        await consume_generation_quota(old,await old.get(User,owner))
        # The old router creates the job in another DB session before committing its quota session.
        job_id=await asyncio.wait_for(job(factory,owner,"queued"),timeout=3)
        assert job_id
        freezing=asyncio.create_task(freeze_owner(factory,owner,admin))
        await asyncio.sleep(0.02)
        assert not freezing.done()
    await asyncio.wait_for(freezing,timeout=3)
    with pytest.raises(ApplicationError,match="legacy_jobs_active"):
        await migrate_owner(factory,owner,admin)
    async with factory() as db:
        assert (await db.get(User,owner)).daily_generation_balance==6

async def test_daily_topup_counts_total_balance_not_available_and_never_repeats(factory):
    owner,_,branch=await setup(factory,balance=5)
    async with factory.begin() as db:
        (await db.get(User,owner)).daily_generation_limit_override=5
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    tomorrow=utc_now()+timedelta(days=1)
    async with factory.begin() as db:
        wallet=await locked_wallet(db,owner)
        await top_up(db,wallet,now=tomorrow)
        assert (wallet.balance,wallet.reserved)==(5,1)
    async with factory.begin() as db:
        await cancel(db,owner,opid)
        wallet=await locked_wallet(db,owner)
        await top_up(db,wallet,now=tomorrow)
        assert (wallet.balance,wallet.reserved)==(5,0)
        records=(await db.execute(select(LedgerEntry).where(
            LedgerEntry.owner_id==owner,LedgerEntry.kind=="topup"))).scalars().all()
        assert len(records)==1 and records[0].balance_delta==0

async def test_topup_preserves_extra_balance_and_fills_deficit_only_once(factory):
    owner,_,_=await setup(factory,balance=25)
    async with factory.begin() as db:
        (await db.get(User,owner)).daily_generation_limit_override=10
        wallet=await locked_wallet(db,owner)
        await top_up(db,wallet,now=utc_now()+timedelta(days=1))
        assert wallet.balance==25
    low,_,_=await setup(factory,balance=3)
    async with factory.begin() as db:
        (await db.get(User,low)).daily_generation_limit_override=10
        wallet=await locked_wallet(db,low)
        await top_up(db,wallet,now=utc_now()+timedelta(days=1))
        await top_up(db,wallet,now=utc_now()+timedelta(days=1))
        assert wallet.balance==10

async def test_adjustment_cannot_spend_reserved_and_replay_grants_once(factory):
    owner,_,branch=await setup(factory,balance=5)
    _,admin=await actors(factory)
    await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    with pytest.raises(ApplicationError,match="quota_insufficient"):
        async with factory.begin() as db:
            await adjust(db,owner,delta=-5,key="adjust-negative",actor_id=admin,reason="Test")
    for _ in range(2):
        async with factory.begin() as db:
            await adjust(db,owner,delta=4,key="adjust-positive",actor_id=admin,reason="Test")
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(9,1)

async def test_technical_refund_capped_by_settlement_and_is_idempotent(factory,tmp_path):
    owner,_,branch=await setup(factory,balance=5)
    _,admin=await actors(factory)
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    token=await running(factory,opid)
    async with factory.begin() as db:
        await publish(db,opid,"worker",token,"main_image",staged(tmp_path),storage_root=tmp_path)
    for _ in range(2):
        async with factory.begin() as db:
            await refund(db,opid,units=1,key="refund-first",actor_id=admin,reason="Technical compensation")
    with pytest.raises(ApplicationError,match="refund_exceeds_settlement"):
        async with factory.begin() as db:
            await refund(db,opid,units=1,key="refund-second",actor_id=admin,reason="Extra")
    async with factory() as db:
        assert (await db.get(Wallet,owner)).balance==5

async def test_adjustment_requires_admin_and_reason(factory):
    owner,_,_=await setup(factory)
    _,admin=await actors(factory)
    with pytest.raises(ApplicationError,match="admin_required"):
        async with factory.begin() as db:
            await adjust(db,owner,delta=1,key="adjust-no-admin",actor_id=owner,reason="Test")
    with pytest.raises(ApplicationError,match="reason_required"):
        async with factory.begin() as db:
            await adjust(db,owner,delta=1,key="adjust-no-reason",actor_id=admin,reason="")

async def test_admin_quote_preserves_exemption_but_authorizes_one_image(factory,tmp_path):
    owner,_,branch=await setup(factory,balance=5)
    async with factory.begin() as db:
        (await db.get(User,owner)).role=UserRole.admin
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    token=await running(factory,opid)
    async with factory.begin() as db:
        await publish(db,opid,"worker",token,"main_image",staged(tmp_path),storage_root=tmp_path)
        assert (await db.get(Operation,opid)).settled_units==0
        assert (await db.get(Wallet,owner)).balance==5

async def test_legacy_quota_still_works_before_v2_schema_install(migration_database):
    await create_legacy(migration_database)
    engine=create_async_engine(migration_database)
    try:
        factory=async_sessionmaker(engine,expire_on_commit=False)
        owner,admin=await actors(factory)
        async with factory.begin() as db:
            state=await consume_generation_quota(db,await db.get(User,owner))
            assert state.remaining==6
    finally:
        await engine.dispose()

async def test_first_action_after_midnight_tops_up_before_settlement(factory,tmp_path,monkeypatch):
    owner,_,branch=await setup(factory,balance=3)
    async with factory.begin() as db:
        (await db.get(User,owner)).daily_generation_limit_override=5
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    token=await running(factory,opid)
    tomorrow=utc_now()+timedelta(days=1)
    monkeypatch.setattr("app.generation.quota.utc_now",lambda:tomorrow)
    async with factory.begin() as db:
        await publish(db,opid,"worker",token,"main_image",staged(tmp_path),storage_root=tmp_path)
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(4,0)
        # Reading the same window must not restore the unit just settled.
        await top_up(db,wallet,now=tomorrow)
        assert wallet.balance==4


async def test_refund_for_deleted_legacy_user_still_returns_false(factory):
    async with factory.begin() as db:
        assert await refund_generation_quota(db, str(uuid4()), current_generation_quota_window()[0]) is False
