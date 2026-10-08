"""Synthetic backup files and isolated PG only; no backup/restore drill."""
import json
import os
import shutil
from datetime import timedelta
from pathlib import Path
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from app.auth.models import UserRole
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import User
from app.design.models import AdminAction
from app.maintenance import backup_retention as retention
from app.maintenance.orphan_assets import CleanupError
from .test_backup_bundle import prepared as source, create
from .test_operations import factory
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints

REASON = "Approved backup retention policy"


async def prepared(factory, tmp_path):
    actor,_,_,sources,settings = await source(factory,tmp_path)
    root = tmp_path/"backups"
    root.mkdir()
    await create(factory,settings,root/"b0",sources)
    for index in range(1,4):
        shutil.copytree(root/"b0",root/f"b{index}")
    now = utc_now()
    for index in range(4):
        path = root/f"b{index}"/"manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["created_at"] = (now-timedelta(days=40-index*10)).isoformat()
        path.write_text(json.dumps(data),encoding="utf-8")
    async with factory.begin() as db:
        (await db.get(User,actor)).role = UserRole.admin
    return actor,root,settings.model_copy(update={"v2_backup_retention_days":7}),now


async def inspect(factory,settings,root,now):
    async with factory.begin() as db:
        return await retention.inspect_backups(db,settings,root,users_stopped=True,now=now)


async def retire(factory,settings,root,actor,now):
    report = await inspect(factory,settings,root,now)
    async with factory.begin() as db:
        result = await retention.retire_backups(db,settings,root,report["digest"],actor,REASON,
                                               users_stopped=True,now=now)
    return report,result["receipt_id"]


async def purge(factory,settings,root,batch,actor,now):
    async with factory.begin() as db:
        return await retention.purge_backups(db,settings,root,batch,actor,REASON,users_stopped=True,now=now)


def test_policy_default_is_disabled_and_two_valid_copies_are_minimum():
    assert Settings(_env_file=None).v2_backup_retention_days == 0
    with pytest.raises(ValidationError):
        Settings(_env_file=None,v2_backup_min_copies=1)


async def test_retirement_precedes_deletion_and_purge_replay_is_idempotent(factory,tmp_path):
    actor,root,settings,now = await prepared(factory,tmp_path)
    before = await fingerprints(factory)
    report,batch = await retire(factory,settings,root,actor,now)
    assert [b["name"] for b in report["keep"]] == ["b3","b2"]
    assert {b["name"] for b in report["candidates"]} == {"b0","b1"}
    assert all((root/f"b{i}").exists() for i in range(4))
    result = await purge(factory,settings,root,batch,actor,now)
    assert result["count"] == 2
    assert sorted(p.name for p in root.iterdir()) == ["b2","b3"]
    assert await purge(factory,settings,root,batch,actor,now) == result
    after = await fingerprints(factory)
    assert {k:v for k,v in before.items() if k!="v2_admin_actions"} == {
        k:v for k,v in after.items() if k!="v2_admin_actions"}


@pytest.mark.parametrize("kind",["incomplete","corrupt","foreign","schema","future","hardlink"])
async def test_unverified_and_unrelated_bundles_are_protected_not_counted(factory,tmp_path,kind):
    actor,root,settings,now = await prepared(factory,tmp_path)
    target = root/"b3"
    if kind == "incomplete":
        (target/"INCOMPLETE").write_text("test",encoding="utf-8")
    elif kind == "corrupt":
        (target/"database.dump").write_bytes(b"corrupt")
    elif kind == "hardlink":
        os.link(target/"database.dump",tmp_path/"linked-dump")
    else:
        path = target/"manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data[{"foreign":"database_identity","schema":"schema","future":"created_at"}[kind]] = {
            "foreign":"f"*64,"schema":"v2_future","future":(now+timedelta(days=2)).isoformat()}[kind]
        path.write_text(json.dumps(data),encoding="utf-8")
    report = await inspect(factory,settings,root,now)
    assert report["protected"] == ["b3"]
    assert [b["name"] for b in report["keep"]] == ["b2","b1"]
    assert [b["name"] for b in report["candidates"]] == ["b0"]


async def test_young_copies_and_minimum_are_kept_even_when_everything_else_is_old(factory,tmp_path):
    actor,root,settings,now = await prepared(factory,tmp_path)
    # A longer policy retains b1 as well as the two newest copies.
    report = await inspect(factory,settings.model_copy(update={"v2_backup_retention_days":35}),root,now)
    assert {b["name"] for b in report["keep"]} == {"b1","b2","b3"}
    report = await inspect(factory,settings.model_copy(update={"v2_backup_min_copies":10}),root,now)
    assert not report["candidates"] and len(report["keep"]) == 4


async def test_disabled_acknowledgement_and_scope_guards(factory,tmp_path):
    actor,root,settings,now = await prepared(factory,tmp_path)
    for chosen,path,ack,code in [
        (settings.model_copy(update={"v2_backup_retention_days":0}),root,True,"backup_retention_disabled"),
        (settings,root,False,"backup_users_stopped"),
        (settings,Path(settings.v2_storage_dir),True,"backup_source_overlap"),
    ]:
        with pytest.raises(CleanupError,match=code):
            async with factory.begin() as db:
                await retention.inspect_backups(db,chosen,path,users_stopped=ack,now=now)


async def test_stale_inventory_and_non_admin_cannot_retire(factory,tmp_path):
    actor,root,settings,now = await prepared(factory,tmp_path)
    report = await inspect(factory,settings,root,now)
    (root/"b0"/"extra").write_bytes(b"new")
    with pytest.raises(CleanupError,match="inventory_changed"):
        async with factory.begin() as db:
            await retention.retire_backups(db,settings,root,report["digest"],actor,REASON,users_stopped=True,now=now)
    async with factory.begin() as db:
        (await db.get(User,actor)).role = UserRole.user
    with pytest.raises(CleanupError,match="admin_required"):
        await retire(factory,settings,root,actor,now)


async def test_retirement_requires_separate_commit_before_unlink(factory,tmp_path):
    actor,root,settings,now = await prepared(factory,tmp_path)
    report = await inspect(factory,settings,root,now)
    with pytest.raises(CleanupError,match="retirement_commit_required"):
        async with factory.begin() as db:
            result = await retention.retire_backups(db,settings,root,report["digest"],actor,REASON,users_stopped=True,now=now)
            await retention.purge_backups(db,settings,root,result["receipt_id"],actor,REASON,users_stopped=True,now=now)
    assert all((root/f"b{i}"/"database.dump").exists() for i in range(4))


@pytest.mark.parametrize("changed",["candidate","retained","policy"])
async def test_changed_candidate_retained_copy_or_policy_blocks_before_deleting(factory,tmp_path,changed):
    actor,root,settings,now = await prepared(factory,tmp_path)
    report,batch = await retire(factory,settings,root,actor,now)
    if changed=="candidate":
        (root/"b0"/"extra").write_bytes(b"do not delete")
    elif changed=="retained":
        (root/"b3"/"database.dump").write_bytes(b"changed retained copy")
    else:
        settings=settings.model_copy(update={"v2_backup_retention_days":30})
    with pytest.raises((CleanupError,retention.backup.BackupError)):
        await purge(factory,settings,root,batch,actor,now)
    assert all((root/f"b{i}"/"database.dump").exists() for i in range(4))


async def test_interrupted_purge_rolls_back_audit_then_resumes_missing_files(factory,tmp_path,monkeypatch):
    actor,root,settings,now = await prepared(factory,tmp_path)
    report,batch = await retire(factory,settings,root,actor,now)
    real_unlink = Path.unlink
    calls = 0
    def interrupted(path,*args,**kwargs):
        nonlocal calls
        if path.is_relative_to(root):
            calls += 1
            if calls == 2:
                raise OSError("synthetic interruption")
        return real_unlink(path,*args,**kwargs)
    with monkeypatch.context() as scope:
        scope.setattr(Path,"unlink",interrupted)
        with pytest.raises(OSError,match="synthetic"):
            await purge(factory,settings,root,batch,actor,now)
    async with factory() as db:
        rows = (await db.scalars(select(AdminAction))).all()
        assert [r.action for r in rows] == ["retire_backups"]
    result = await purge(factory,settings,root,batch,actor,now)
    assert result["count"] == 2
    assert sorted(p.name for p in root.iterdir()) == ["b2","b3"]


async def test_real_backup_module_and_rotation_share_storage_lock(factory,tmp_path):
    from app.maintenance.backup_bundle import freeze
    actor,root,settings,now = await prepared(factory,tmp_path)
    async with factory.begin() as db:
        await freeze(db)
        with pytest.raises(CleanupError,match="storage_writer_busy"):
            await inspect(factory,settings,root,now)
