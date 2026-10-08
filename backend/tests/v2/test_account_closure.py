"""Account identity lifecycle using disposable PostgreSQL only."""
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

from app.admin.v2_controls import set_control
from app.auth.models import UserRole
from app.auth.sessions import _hash_token, get_current_user_and_session
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import AuthSession, User
from app.design.models import AccountClosure, Design, EditorDraft, Wallet
from app.maintenance import account_closure as module
from app.maintenance.orphan_assets import CleanupError
from .test_operations import factory, setup as account
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints

REASON="Verified owner request through support"


async def prepared(factory):
    owner,design_id,branch=await account(factory)
    now=utc_now()
    async with factory.begin() as db:
        admin=User(email=str(uuid4())+"@example.com",role=UserRole.admin)
        db.add(admin);await db.flush()
        user=await db.get(User,owner)
        user.password_hash="synthetic-password-hash"
        user.generation_quality_improvement_consent=True
        db.add(AuthSession(user_id=owner,session_token_hash=_hash_token("synthetic-token"),
            expires_at=now+timedelta(days=20),created_ip="127.0.0.1",user_agent="synthetic"))
        await set_control(db,"global",True)
        admin_id=admin.id
    cfg=Settings(_env_file=None,v2_enabled=True,app_env="test",v2_runtime_mode="local",
        v2_account_closure_enabled=True,v2_account_closure_grace_days=2)
    return owner,admin_id,design_id,cfg,now


async def inspect(factory,cfg,owner):
    async with factory.begin() as db:return await module.inspect_account(db,cfg,owner,writers_stopped=True)


async def request(factory,cfg,owner,admin,now,report):
    async with factory.begin() as db:return await module.request_closure(db,cfg,owner,report["digest"],admin,REASON,
        writers_stopped=True,now=now)


async def close(factory,cfg,owner,admin,now,request_id):
    async with factory.begin() as db:return await module.close_account(db,cfg,owner,request_id,admin,REASON,
        writers_stopped=True,now=now)


async def test_request_revokes_sessions_cancel_preserves_data_and_prior_ban(factory):
    owner,admin,design_id,cfg,now=await prepared(factory)
    report=await inspect(factory,cfg,owner)
    assert "example.com" not in str(report) and "password" not in str(report)
    result=await request(factory,cfg,owner,admin,now,report)
    assert await request(factory,cfg,owner,admin,now,report)==result
    async with factory() as db:
        assert (await db.get(User,owner)).is_banned
        assert not (await db.get(User,owner)).generation_quality_improvement_consent
        assert (await db.scalar(select(AuthSession).where(AuthSession.user_id==owner))).revoked_at==now
        assert (await db.get(Design,design_id)).deleted_at is None
    async with factory.begin() as db:
        await module.cancel_closure(db,cfg,owner,result["request_id"],admin,REASON,writers_stopped=True,now=now+timedelta(days=1))
    async with factory() as db:
        assert not (await db.get(User,owner)).is_banned
        assert not (await db.get(User,owner)).generation_quality_improvement_consent
        assert (await db.get(AccountClosure,owner)).state=="cancelled"
        assert (await db.scalar(select(AuthSession).where(AuthSession.user_id==owner))).revoked_at is not None
    async with factory.begin() as db:(await db.get(User,owner)).is_banned=True
    again=await request(factory,cfg,owner,admin,now+timedelta(days=1),await inspect(factory,cfg,owner))
    assert again["request_id"] != result["request_id"]
    async with factory.begin() as db:
        await module.cancel_closure(db,cfg,owner,again["request_id"],admin,REASON,writers_stopped=True,now=now+timedelta(days=2))
    async with factory() as db:assert (await db.get(User,owner)).is_banned


async def test_closure_erases_login_identity_queues_designs_preserves_finance(factory):
    owner,admin,design_id,cfg,now=await prepared(factory)
    before=await fingerprints(factory)
    result=await request(factory,cfg,owner,admin,now,await inspect(factory,cfg,owner))
    with pytest.raises(CleanupError,match="grace"):await close(factory,cfg,owner,admin,now,result["request_id"])
    final=await close(factory,cfg,owner,admin,now+timedelta(days=3),result["request_id"])
    assert final["identity_erased"] is True and final["data_erased"] is False and final["backups_erased"] is False
    assert await close(factory,cfg,owner,admin,now+timedelta(days=3),result["request_id"])==final
    async with factory() as db:
        user=await db.get(User,owner)
        assert user.email=="closed+"+owner+"@kigcraft.invalid" and user.password_hash is None and user.is_banned
        assert not await db.scalar(select(AuthSession.id).where(AuthSession.user_id==owner))
        design=await db.get(Design,design_id)
        assert design.deleted_at < design.trash_restore_until <= now+timedelta(days=3)
        assert (await db.get(AccountClosure,owner)).state=="closed"
    after=await fingerprints(factory)
    for name in ("v2_ledger","v2_wallets","v2_quotes"):assert before[name]==after[name]


@pytest.mark.parametrize("stage",["pending","closed"])
async def test_database_guard_rejects_unban_and_discarding_closed_markers(factory,stage):
    owner,admin,_,cfg,now=await prepared(factory)
    result=await request(factory,cfg,owner,admin,now,await inspect(factory,cfg,owner))
    if stage=="closed":await close(factory,cfg,owner,admin,now+timedelta(days=3),result["request_id"])
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:await db.execute(update(User).where(User.id==owner).values(is_banned=False))
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:await db.execute(text("DELETE FROM v2_account_closures WHERE owner_id=:id"),{"id":owner})
    if stage=="closed":
        with pytest.raises(DBAPIError):
            async with factory.begin() as db:
                await db.execute(update(AccountClosure).where(AccountClosure.owner_id==owner).values(state="cancelled",completed_at=None,cancelled_at=now))


@pytest.mark.parametrize("fault",["disabled","policy","identity","admin","stop"])
async def test_request_refuses_without_mutating_identity(factory,fault):
    owner,admin,_,cfg,now=await prepared(factory)
    report=await inspect(factory,cfg,owner)
    if fault=="disabled":cfg.v2_account_closure_enabled=False
    elif fault=="policy":cfg.v2_account_closure_grace_days=30
    elif fault=="identity":
        async with factory.begin() as db:(await db.get(User,owner)).email="changed@example.com"
    elif fault=="admin":
        async with factory.begin() as db:(await db.get(User,owner)).role=UserRole.admin
    with pytest.raises(CleanupError):
        async with factory.begin() as db:
            await module.request_closure(db,cfg,owner,report["digest"],admin,REASON,writers_stopped=fault!="stop",now=now)
    async with factory() as db:
        assert not (await db.get(User,owner)).is_banned
        assert await db.get(AccountClosure,owner) is None


async def test_audit_failure_rolls_back_closure_even_if_caller_commits(factory,monkeypatch):
    owner,admin,design_id,cfg,now=await prepared(factory)
    result=await request(factory,cfg,owner,admin,now,await inspect(factory,cfg,owner))
    before=await fingerprints(factory)
    async def fail(*args,**kwargs):raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(module,"receipt",fail)
    async with factory.begin() as db:
        with pytest.raises(RuntimeError,match="synthetic"):
            await module.close_account(db,cfg,owner,result["request_id"],admin,REASON,
                writers_stopped=True,now=now+timedelta(days=3))
    assert await fingerprints(factory)==before


async def test_new_longer_policy_delays_final_close_and_stale_request_rejected(factory):
    owner,admin,_,cfg,now=await prepared(factory)
    result=await request(factory,cfg,owner,admin,now,await inspect(factory,cfg,owner))
    cfg.v2_account_closure_grace_days=30
    with pytest.raises(CleanupError,match="grace"):await close(factory,cfg,owner,admin,now+timedelta(days=3),result["request_id"])
    with pytest.raises(CleanupError,match="mismatch"):await close(factory,cfg,owner,admin,now+timedelta(days=31),str(uuid4()))


async def test_authentication_and_admin_unban_observe_closure(factory,monkeypatch):
    from fastapi import HTTPException
    from app.core.config import get_settings
    import app.admin.router as router
    from types import SimpleNamespace
    owner,admin,_,cfg,now=await prepared(factory)
    result=await request(factory,cfg,owner,admin,now,await inspect(factory,cfg,owner))
    cookie=get_settings().session_cookie_name+"=synthetic-token"
    req=Request({"type":"http","headers":[(b"cookie",cookie.encode())]})
    async with factory() as db:
        with pytest.raises(HTTPException) as error:
            await get_current_user_and_session(req,db)
        assert error.value.status_code==401
        actor=await db.get(User,admin)
        monkeypatch.setattr(router,"get_settings",lambda:cfg)
        with pytest.raises(HTTPException) as error:
            await router.update_admin_user_ban(owner,SimpleNamespace(is_banned=False),actor,db)
        assert error.value.status_code==409
        from app.design.models import AdminAction
        receipt=await db.get(AdminAction,result["receipt_id"])
        assert receipt.owner_id==owner and receipt.target_id==owner


async def test_closure_feeds_design_content_retirement(factory):
    from app.maintenance import design_content
    owner,admin,design_id,cfg,now=await prepared(factory)
    result=await request(factory,cfg,owner,admin,now,await inspect(factory,cfg,owner))
    await close(factory,cfg,owner,admin,now+timedelta(days=3),result["request_id"])
    cfg.v2_design_content_cleanup_enabled=True
    async with factory.begin() as db:
        report=await design_content.inspect_designs(db,cfg,writers_stopped=True,now=now+timedelta(days=4))
        assert [item["design_id"] for item in report["candidates"]]==[design_id]
        await design_content.scrub_designs(db,cfg,report["digest"],admin,REASON,
            writers_stopped=True,now=now+timedelta(days=4))
    async with factory() as db:assert (await db.get(Design,design_id)).content_purged_at is not None


async def test_close_requires_committed_request(factory):
    owner,admin,_,cfg,now=await prepared(factory)
    report=await inspect(factory,cfg,owner)
    async with factory.begin() as db:
        result=await module.request_closure(db,cfg,owner,report["digest"],admin,REASON,writers_stopped=True,now=now)
        with pytest.raises(CleanupError,match="commit"):
            await module.close_account(db,cfg,owner,result["request_id"],admin,REASON,
                writers_stopped=True,now=now+timedelta(days=3))
    async with factory() as db:assert (await db.get(AccountClosure,owner)).state=="pending"


async def test_migration_refuses_to_discard_closure_marker(factory,migration_database):
    import os,subprocess,sys
    from .test_migrations import ROOT
    owner,admin,_,cfg,now=await prepared(factory)
    await request(factory,cfg,owner,admin,now,await inspect(factory,cfg,owner))
    result=subprocess.run([sys.executable,"-m","alembic","downgrade","v2_0025"],cwd=ROOT,
        env={**os.environ,"V2_DATABASE_URL":migration_database,"PYTHONIOENCODING":"utf-8"},
        capture_output=True,text=True,encoding="utf-8",timeout=30)
    assert result.returncode != 0 and "Refuse to discard account closure history" in result.stderr
    async with factory() as db:
        assert (await db.get(AccountClosure,owner)).state=="pending"
        assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"


@pytest.mark.parametrize("runtime_status",["running","failed"])
async def test_legacy_recorded_terminal_must_match_runtime_state(factory,runtime_status):
    from app.db.models import Project,GenerationJob,JobStatus
    owner,admin,_,cfg,now=await prepared(factory)
    async with factory.begin() as db:
        project=Project(user_id=owner,name="legacy")
        db.add(project);await db.flush()
        db.add(GenerationJob(project_id=project.id,user_id=owner,status=JobStatus.failed,runtime_status=runtime_status))
    if runtime_status=="running":
        with pytest.raises(CleanupError,match="legacy_operation_unconfirmed"):await inspect(factory,cfg,owner)
    else:
        assert (await inspect(factory,cfg,owner))["owner_id"]==owner
