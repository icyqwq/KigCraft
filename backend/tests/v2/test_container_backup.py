"""Multi-account backup components: synthetic native history, fake daemon/dump/restore, isolated PG."""
from contextlib import asynccontextmanager
from datetime import timedelta
import json
from pathlib import Path
import shutil
from uuid import uuid4
import pytest
from app.admin.v2_controls import set_control
from app.auth.models import UserRole
from app.assets.storage import upload_in_transaction
from app.codex_runtime.profile_lock import ProfileLease, ProfileLockError
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import User
from app.design.models import CodexEpoch, Operation
from app.maintenance import backup_bundle as backup, container_backup as production
from app.maintenance import backup_retention as retention, restore_bundle as restore
from app.maintenance.backup_bundle import BackupError
from app.maintenance.orphan_assets import CleanupError
from .test_operations import factory, setup, quoted, submit_request
from .test_migrations import migration_database
from .test_backup_bundle import fake_dump
from .test_codex_snapshot import profile as native_profile, VERSION
from .test_orphan_assets import png
from .test_restore_bundle import fake_create, fake_import, fake_validate
from .test_legacy_inventory import fingerprints


class StoppedRuntime:
    def __init__(self, settings, base, states):
        self.owner=settings.v2_worker_user_id
        self.profile=base/self.owner/"profile"
        self.work_root=base/self.owner/"work"
        self.input_root=base/self.owner/"inputs"
        self.daemon_id="synthetic-daemon"
        self.states=states
        self.held=False
    @asynccontextmanager
    async def operation(self):
        lease=ProfileLease(self.profile).acquire()
        self.held=True
        try:
            await self.verify_stopped()
            yield
        finally:
            self.held=False
            lease.release(clean=not self.states.get(self.owner,False))
    async def verify_stopped(self):
        assert self.held
        if self.states.get(self.owner,False):
            raise BackupError("container_not_stopped")


async def prepared(factory,tmp_path,monkeypatch):
    base=tmp_path/"runtime-accounts";base.mkdir()
    assets=tmp_path/"assets";assets.mkdir()
    owners=[];ids={};operations={};states={}
    for index in range(2):
        owner,_,branch=await setup(factory)
        owners.append(owner)
        op=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
        operations[owner]=op
        parent=base/owner;parent.mkdir()
        home,threads=native_profile.__wrapped__(parent)
        home.rename(parent/"profile")
        home=parent/"profile"
        # The synthetic native index must point at its actual fixture location.
        import sqlite3
        with sqlite3.connect(home/"state_5.sqlite") as db:
            for identity in threads:
                db.execute("UPDATE threads SET rollout_path=?,cwd=? WHERE id=?",
                    (str(home/"sessions"/f"rollout-{identity}.jsonl"),str(parent/"work"),identity))
        db.close()
        ids[owner]=threads
        workspace=parent/"work"/op;workspace.mkdir(parents=True)
        (workspace/"result.txt").write_text(f"account-{index}",encoding="utf-8")
        inputs=parent/"inputs"/op/"inputs";inputs.mkdir(parents=True)
        (inputs/"reference.png").write_bytes(png())
        async with factory.begin() as db:
            db.add(CodexEpoch(branch_id=branch,thread_id=threads[0],runtime_identity=str(index)*64,
                model="gpt-6.1-sol",codex_version=VERSION))
            db.add(await upload_in_transaction(db,png(),owner,assets))
    async with factory.begin() as db:
        await set_control(db,"global",True)
    settings=Settings(_env_file=None,app_env="production",v2_enabled=True,v2_runtime_mode="container",
        v2_container_user_ids=owners,v2_container_root="/srv/kigcraft/runtime",
        v2_container_image="runtime@sha256:"+"a"*64,v2_egress_image="egress@sha256:"+"b"*64,
        v2_egress_hosts=["model.example.com"],v2_storage_dir=str(assets),v2_codex_version=VERSION)
    # Simulate only the Linux filesystem root; policy and manifests retain POSIX paths.
    canonical=production.canonical_root
    monkeypatch.setattr(production,"canonical_root",
        lambda path:base if str(path)==settings.v2_container_root else canonical(path))
    create_runtime=lambda cfg:StoppedRuntime(cfg,base,states)
    return settings,{"assets":assets},base,owners,ids,operations,states,create_runtime


async def create(factory,cfg,roots,destination,runtime,**options):
    async with factory.begin() as db:
        return await production.create_container_bundle(db,cfg,destination,roots,
            writers_stopped=True,dump=options.pop("dump",fake_dump),runtime_factory=runtime,**options)


async def test_two_accounts_backup_restore_separate_histories_inputs_and_no_profile_secrets(factory,tmp_path,monkeypatch):
    cfg,roots,base,owners,ids,ops,states,runtime=await prepared(factory,tmp_path,monkeypatch)
    before=await fingerprints(factory)
    source=tmp_path/"backup"
    def locked_dump(*args):
        for owner in owners:
            with pytest.raises(ProfileLockError,match="runtime_profile_busy"):
                ProfileLease(base/owner/"profile").acquire()
        fake_dump(*args)
    result=await create(factory,cfg,roots,source,runtime,dump=locked_dump)
    manifest=backup.verify_bundle(source,expected_manifest_sha256=result["manifest_sha256"])
    assert manifest["scope"]==backup.CONTAINER_SCOPE and set(manifest["accounts"])==set(owners)
    for owner in owners:
        assert manifest["accounts"][owner]["native_threads"]==[ids[owner][0]]
        keys=[key for key in manifest["files"] if key.startswith(f"accounts/{owner}/")]
        assert f"accounts/{owner}/inputs/{ops[owner]}/inputs/reference.png" in keys
        assert f"accounts/{owner}/work/{ops[owner]}/result.txt" in keys
        assert not any("auth.json" in key or "config.toml" in key or ids[owner][1] in key for key in keys)
        assert ProfileLease(base/owner/"profile").inspect()["state"]=="closed"
    destination=tmp_path/"restore"
    output=await restore.restore_bundle(source,destination,"postgresql://test@localhost/postgres",
        result["manifest_sha256"],create_database=fake_create,restore=fake_import,validate=fake_validate)
    assert output["status"]=="restored_execution_disabled"
    for owner in owners:
        assert (destination/"accounts"/owner/"profile"/"state_5.sqlite").is_file()
        assert not (destination/"accounts"/owner/"profile"/"auth.json").exists()
        assert (destination/"accounts"/owner/"work"/ops[owner]/"result.txt").is_file()
    assert 'V2_RUNTIME_MODE="disabled"' in (destination/"execution-disabled.env.example").read_text()
    assert await fingerprints(factory)==before


@pytest.mark.parametrize("fault",["extra_account","missing_account","foreign_work","cross_thread","running","busy"])
async def test_account_boundaries_refuse_without_creating_artifact(factory,tmp_path,monkeypatch,fault):
    cfg,roots,base,owners,ids,ops,states,runtime=await prepared(factory,tmp_path,monkeypatch)
    lock=None
    if fault=="extra_account":(base/str(uuid4())).mkdir()
    elif fault=="missing_account":cfg.v2_container_user_ids=owners[:1]
    elif fault=="foreign_work":(base/owners[0]/"work"/ops[owners[1]]).mkdir()
    elif fault=="cross_thread":
        async with factory.begin() as db:
            (await db.get(Operation,ops[owners[1]])).context_before={"thread_id":ids[owners[0]][0]}
    elif fault=="running":states[owners[1]]=True
    else:lock=ProfileLease(base/owners[1]/"profile").acquire()
    try:
        with pytest.raises((BackupError,ProfileLockError)):
            await create(factory,cfg,roots,tmp_path/"backup",runtime)
        assert not (tmp_path/"backup").exists()
    finally:
        if lock:lock.release(clean=True)


async def test_running_state_during_copy_keeps_incomplete_and_uncertain_marker(factory,tmp_path,monkeypatch):
    cfg,roots,base,owners,ids,ops,states,runtime=await prepared(factory,tmp_path,monkeypatch)
    def changed(*args):
        fake_dump(*args)
        states[owners[0]]=True
    with pytest.raises(BackupError,match="container_not_stopped"):
        await create(factory,cfg,roots,tmp_path/"backup",runtime,dump=changed)
    assert (tmp_path/"backup"/"INCOMPLETE").is_file()
    assert ProfileLease(base/owners[0]/"profile").inspect()["state"]=="opening"


async def test_production_retention_keeps_two_complete_bundles_and_rejects_local_receipt_scope(factory,tmp_path,monkeypatch):
    cfg,roots,base,owners,ids,ops,states,runtime=await prepared(factory,tmp_path,monkeypatch)
    root=tmp_path/"backups";root.mkdir()
    await create(factory,cfg,roots,root/"b0",runtime)
    now=utc_now()
    for index in range(3):
        path=root/f"b{index}"
        if index:shutil.copytree(root/"b0",path)
        data=json.loads((path/"manifest.json").read_text(encoding="utf-8"))
        data["created_at"]=(now-timedelta(days=40-index*10)).isoformat()
        (path/"manifest.json").write_text(json.dumps(data),encoding="utf-8")
    actor=owners[0]
    async with factory.begin() as db:(await db.get(User,actor)).role=UserRole.admin
    cfg.v2_backup_retention_days=7
    async with factory.begin() as db:
        report=await retention.inspect_backups(db,cfg,root,users_stopped=True,now=now)
        assert report["scope"]==retention.CONTAINER_SCOPE
        assert len(report["keep"])==2 and len(report["candidates"])==1
        result=await retention.retire_backups(db,cfg,root,report["digest"],actor,"production retention",
                                            users_stopped=True,now=now)
    batch=result["receipt_id"]
    local=cfg.model_copy(update={"app_env":"test","v2_runtime_mode":"local",
                                "v2_runtime_dir":str(tmp_path/"unused-local")})
    with pytest.raises(CleanupError,match="retirement_scope_mismatch"):
        async with factory.begin() as db:
            await retention.purge_backups(db,local,root,batch,actor,"production retention",users_stopped=True,now=now)
    async with factory.begin() as db:
        result=await retention.purge_backups(db,cfg,root,batch,actor,"production retention",users_stopped=True,now=now)
    assert result["count"]==1 and not (root/"b0").exists()
    assert backup.verify_bundle(root/"b1")["scope"]==backup.CONTAINER_SCOPE
    assert backup.verify_bundle(root/"b2")["scope"]==backup.CONTAINER_SCOPE


async def test_restored_two_account_database_is_quarantined_without_requeue(factory,tmp_path,monkeypatch):
    from app.design.models import RuntimeControl, Wallet
    cfg,roots,base,owners,ids,ops,states,runtime=await prepared(factory,tmp_path,monkeypatch)
    async with factory.begin() as db:
        (await db.get(Operation,ops[owners[0]])).state="reconciling"
    source=tmp_path/"backup"
    await create(factory,cfg,roots,source,runtime)
    manifest=backup.verify_bundle(source)
    before=await fingerprints(factory)
    async with factory.begin() as db:
        result=await restore.verify_and_guard(db,manifest)
    assert result["quarantined_accounts"]==2 and result["operations_requeued"] is False
    after=await fingerprints(factory)
    assert {k:v for k,v in before.items() if k!="v2_runtime_controls"}=={
        k:v for k,v in after.items() if k!="v2_runtime_controls"}
    async with factory() as db:
        for owner in owners:
            assert (await db.get(RuntimeControl,"owner:"+owner)).blocked
            assert (await db.get(Wallet,owner)).balance==4
        assert (await db.get(Operation,ops[owners[0]])).state=="reconciling"
