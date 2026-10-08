"""Component checks only: temporary fixtures, isolated PG; no Codex or E2E."""
import os
from datetime import timedelta
from pathlib import Path
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from app.auth.models import UserRole
from app.admin.v2_controls import set_control
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import User
from app.design.models import AdminAction, Asset, Operation, Revision
from app.maintenance import runtime_retention as retention
from app.maintenance.backup_bundle import dependencies, BackupError
from app.maintenance.orphan_assets import CleanupError
from app.operations.cancel import cancel
from app.operations.publish import publish
from app.operations.lifecycle import finish
from .test_migrations import migration_database
from .test_operations import factory, setup, quoted, submit_request, running, staged
from .test_legacy_inventory import fingerprints

REASON = "Approved temporary retention policy"


async def prepared(factory, tmp_path, *, delivered=False):
    owner, _, branch = await setup(factory)
    opid = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    root, storage = tmp_path / "runtime", tmp_path / "assets"
    storage.mkdir()
    workspace = root / opid
    image = staged(workspace)
    now = utc_now()
    if delivered:
        token = await running(factory, opid)
        async with factory.begin() as db:
            await publish(db, opid, "worker", token, "main_image", image, storage_root=storage, finalize=False)
            await finish(db, opid, "worker", token, upstream_status="completed")
    async with factory.begin() as db:
        if not delivered:
            await cancel(db, owner, opid)
        operation = await db.get(Operation, opid)
        operation.staged_outputs = {"main_image":{"filename":image.path.name,
            "delivery_key":image.delivery_key,"sha256":image.sha256}}
        operation.updated_at = now - timedelta(days=30)
        (await db.get(User, owner)).role = UserRole.admin
        await set_control(db, "global", True)
    (workspace / "review" / "nested").mkdir(parents=True)
    (workspace / "review" / "nested" / "temporary.txt").write_text("review fixture",encoding="utf-8")
    (workspace / "inputs").mkdir()
    (workspace / "inputs" / "reference.png").write_bytes(b"keep input")
    (workspace / "original.png").write_bytes(b"keep native output")
    (workspace / "staging" / "unknown.bin").write_bytes(b"keep unrecognized")
    old = (now - timedelta(days=30)).timestamp()
    for path in workspace.rglob("*"):
        if path.is_file():
            os.utime(path, (old, old))
    settings = Settings(_env_file=None, v2_enabled=True, app_env="test", v2_runtime_mode="local",
        v2_runtime_dir=str(root), v2_storage_dir=str(storage), v2_runtime_retention_days=7)
    return owner, opid, settings, now, workspace, image


async def inspect(factory, settings, **kwargs):
    async with factory.begin() as db:
        return await retention.inspect_runtime(db, settings, writers_stopped=True, **kwargs)


async def retire(factory, settings, owner, report, **kwargs):
    async with factory.begin() as db:
        return await retention.retire_runtime(db, settings, report["digest"], owner, REASON,
                                               writers_stopped=True, **kwargs)


async def purge(factory, settings, owner, batch, **kwargs):
    async with factory.begin() as db:
        return await retention.purge_runtime(db, settings, batch["receipt_id"], owner, REASON,
                                              writers_stopped=True, **kwargs)


@pytest.mark.parametrize("delivered", [False, True])
async def test_retirement_commit_purge_and_backup_preserve_history(factory, tmp_path, delivered):
    owner, opid, settings, now, workspace, image = await prepared(factory, tmp_path, delivered=delivered)
    before = await fingerprints(factory)
    report = await inspect(factory, settings, now=now)
    assert len(report["candidates"]) == 2
    assert await fingerprints(factory) == before
    batch = await retire(factory, settings, owner, report, now=now)
    assert image.path.exists()
    result = await purge(factory, settings, owner, batch, now=now)
    assert result["count"] == 2 and not image.path.exists()
    assert await purge(factory, settings, owner, batch, now=now) == result
    assert not (workspace/"review").exists()
    assert (workspace/"inputs"/"reference.png").read_bytes() == b"keep input"
    assert (workspace/"original.png").read_bytes() == b"keep native output"
    assert (workspace/"staging"/"unknown.bin").exists()
    after = await fingerprints(factory)
    assert {k:v for k,v in after.items() if k!="v2_admin_actions"} == {
        k:v for k,v in before.items() if k!="v2_admin_actions"}
    async with factory.begin() as db:
        # Test only the dependency inventory, never pg_dump/restore or real Codex.
        operation = await db.get(Operation, opid)
        # The synthetic running() fixture uses a non-UUID thread ID; replace it
        # for this dependency-only assertion as production thread IDs are UUIDs.
        operation.upstream_thread_id = None
        await db.flush()
        files, _, absent = await dependencies(db, {"runtime":workspace.parent,"assets":Path(settings.v2_storage_dir)})
        assert any(x.get("state")=="retired_temporary_file" for x in absent)
        assert not any(key.endswith(image.path.name) for key in files)
        if delivered:
            assert any(key.startswith("assets/") for key in files)
    assert not (await inspect(factory, settings, now=now))["candidates"]


@pytest.mark.parametrize("failure", ["disabled", "not_paused", "running", "unknown", "lease", "reservation"])
async def test_active_or_unapproved_work_is_never_cleaned(factory,tmp_path,failure):
    owner,opid,settings,now,workspace,image = await prepared(factory,tmp_path)
    async with factory.begin() as db:
        operation = await db.get(Operation,opid)
        if failure=="disabled": settings.v2_runtime_retention_days=0
        if failure=="not_paused": await set_control(db,"global",False)
        if failure=="running": operation.state="publishing"
        if failure=="unknown": operation.dispatch_intent_at=now; operation.upstream_status=None
        if failure=="lease": operation.lease_owner="stale-worker"
        if failure=="reservation": operation.reservation_closed=False
    with pytest.raises(CleanupError):
        await inspect(factory,settings,now=now)
    assert image.path.exists()


async def test_missing_stop_ack_and_non_admin_are_refused(factory,tmp_path):
    owner,_,settings,now,_,image = await prepared(factory,tmp_path)
    async with factory.begin() as db:
        with pytest.raises(CleanupError,match="writers_stopped"):
            await retention.inspect_runtime(db,settings)
    report=await inspect(factory,settings,now=now)
    async with factory.begin() as db:
        (await db.get(User,owner)).role=UserRole.user
    with pytest.raises(CleanupError,match="admin_required"):
        await retire(factory,settings,owner,report,now=now)
    assert image.path.exists()


async def test_changed_inventory_recent_files_and_missing_stage(factory,tmp_path):
    owner,_,settings,now,workspace,image=await prepared(factory,tmp_path)
    report=await inspect(factory,settings,now=now)
    (workspace/"review"/"recent.txt").write_text("recent",encoding="utf-8")
    with pytest.raises(CleanupError,match="inventory_changed"):
        await retire(factory,settings,owner,report,now=now)
    new=await inspect(factory,settings,now=now)
    assert new["retained"][0]["reason"]=="file_recent"
    image.path.unlink()
    with pytest.raises(CleanupError,match="staging_dependency_missing"):
        await inspect(factory,settings,now=now)
    async with factory() as db:
        with pytest.raises(BackupError,match="staging_dependency_missing"):
            await dependencies(db,{"runtime":workspace.parent,"assets":Path(settings.v2_storage_dir)})


async def test_purge_requires_durable_retirement(factory,tmp_path):
    owner,_,settings,now,_,image=await prepared(factory,tmp_path)
    report=await inspect(factory,settings,now=now)
    async with factory.begin() as db:
        batch=await retention.retire_runtime(db,settings,report["digest"],owner,REASON,writers_stopped=True,now=now)
        with pytest.raises(CleanupError,match="retirement_commit_required"):
            await retention.purge_runtime(db,settings,batch["receipt_id"],owner,REASON,writers_stopped=True,now=now)
    assert image.path.exists()


async def test_partial_purge_rollback_can_resume(factory,tmp_path,monkeypatch):
    owner,_,settings,now,workspace,image=await prepared(factory,tmp_path)
    batch=await retire(factory,settings,owner,await inspect(factory,settings,now=now),now=now)
    original=Path.unlink
    calls=[]
    def interrupted(path,*args,**kwargs):
        calls.append(path)
        if len(calls)==2:
            raise OSError("injected interruption")
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,"unlink",interrupted)
        with pytest.raises(OSError):
            await purge(factory,settings,owner,batch,now=now)
    assert len(calls)==2
    result=await purge(factory,settings,owner,batch,now=now)
    assert result["count"]==2 and not image.path.exists()


@pytest.mark.parametrize("change",["bytes","mtime","operation","descriptor","master"])
async def test_revalidation_before_any_removal(factory,tmp_path,change):
    owner,opid,settings,now,workspace,image=await prepared(factory,tmp_path,delivered=True)
    batch=await retire(factory,settings,owner,await inspect(factory,settings,now=now),now=now)
    if change=="bytes":
        image.path.write_bytes(b"changed")
    elif change=="mtime":
        os.utime(image.path,None)
    else:
        async with factory.begin() as db:
            operation=await db.get(Operation,opid)
            if change=="operation": operation.updated_at=now
            elif change=="descriptor": operation.staged_outputs={}
            else:
                revision=await db.scalar(select(Revision).where(Revision.operation_id==opid))
                asset=await db.get(Asset,revision.clean_asset_id)
                (Path(settings.v2_storage_dir)/asset.object_key).unlink()
    with pytest.raises((CleanupError,FileNotFoundError)):
        await purge(factory,settings,owner,batch,now=now)
    assert image.path.exists() and (workspace/"review"/"nested"/"temporary.txt").exists()


async def test_hardlinks_and_busy_registry_fail_closed(factory,tmp_path):
    owner,_,settings,now,workspace,image=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        await db.execute(text("LOCK TABLE v2_operations IN ROW EXCLUSIVE MODE"))
        with pytest.raises(DBAPIError):
            await inspect(factory,settings,now=now)
    os.link(image.path,workspace/"external-copy.png")
    with pytest.raises(CleanupError,match="linked_runtime_file"):
        await inspect(factory,settings,now=now)
    assert image.path.exists()


async def test_terminal_unknown_context_is_retained_and_pagination_validated(factory,tmp_path):
    owner,opid,settings,now,workspace,image=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        (await db.get(Operation,opid)).context_allocation={"state":"unknown"}
    report=await inspect(factory,settings,now=now,limit=1)
    assert report["candidates"]==[]
    assert report["retained"]==[{"operation_id":opid,"reason":"operation_unconfirmed"}]
    with pytest.raises(CleanupError,match="invalid_retention_page"):
        await inspect(factory,settings,now=now,limit=0)
    with pytest.raises(CleanupError,match="invalid_retention_page"):
        await inspect(factory,settings,now=now,after="../other")
    assert image.path.exists()


async def test_retirement_integrity_is_required_for_backup_and_purge(factory,tmp_path):
    owner,_,settings,now,workspace,image=await prepared(factory,tmp_path)
    batch=await retire(factory,settings,owner,await inspect(factory,settings,now=now),now=now)
    async with factory.begin() as db:
        row=await db.get(AdminAction,batch["receipt_id"])
        value={**row.result["manifest"],"digest":"0"*64}
        row.result={**row.result,"manifest":value}
    with pytest.raises(CleanupError,match="invalid_retirement_manifest"):
        await purge(factory,settings,owner,batch,now=now)
    assert image.path.exists()
    image.path.unlink()
    async with factory() as db:
        with pytest.raises(CleanupError,match="invalid_retirement_manifest"):
            await dependencies(db,{"runtime":workspace.parent,"assets":Path(settings.v2_storage_dir)})
