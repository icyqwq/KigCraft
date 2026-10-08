"""Orchestration development checks; pg_dump is substituted, never a restore drill."""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import pytest
from sqlalchemy import select
from app.admin.v2_controls import set_control
from app.assets.storage import upload_in_transaction
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import AlbumItem, ReferenceAsset
from app.design.models import Asset, CodexEpoch, Operation
from app.maintenance import backup_bundle as backup
from app.maintenance.backup_bundle import BackupError
from app.maintenance.codex_snapshot import SnapshotError, digest
from .test_migrations import migration_database
from .test_operations import factory, setup, quoted, submit_request
from .test_orphan_assets import png
from .test_legacy_inventory import fingerprints
from .test_codex_snapshot import profile, VERSION

def fake_dump(executable,url,snapshot,destination,major):
    assert snapshot and major>=14
    destination.write_bytes(b"PGDMP-unit-test-boundary")

async def prepared(factory,tmp_path):
    owner,design,branch=await setup(factory)
    roots={"assets":tmp_path/"assets","runtime":tmp_path/"runtime"}
    for root in roots.values():root.mkdir()
    async with factory.begin() as db:
        db.add(await upload_in_transaction(db,png(),owner,roots["assets"]))
        await set_control(db,"global",True)
    settings=Settings(_env_file=None,v2_enabled=True,v2_runtime_mode="local",app_env="test",
        v2_storage_dir=str(roots["assets"]),v2_runtime_dir=str(roots["runtime"]),v2_codex_version=VERSION)
    return owner,design,branch,roots,settings

async def create(factory,settings,destination,roots,**kwargs):
    async with factory.begin() as db:
        return await backup.create_bundle(db,settings,destination,roots,
            writers_stopped=True,dump=fake_dump,**kwargs)

async def test_assemble_and_verify_all_tables_files_and_owned_native_history(factory,tmp_path,profile):
    owner,_,branch,roots,settings=await prepared(factory,tmp_path)
    home,ids=profile
    async with factory.begin() as db:
        db.add(CodexEpoch(branch_id=branch,thread_id=ids[0],runtime_identity="a"*64,
            model="gpt-6.1-sol",codex_version=VERSION))
    before=await fingerprints(factory)
    result=await create(factory,settings,tmp_path/"bundle",roots,codex_home=home)
    manifest=backup.verify_bundle(tmp_path/"bundle",expected_manifest_sha256=result["manifest_sha256"])
    assert set(manifest["tables"])==set(before)
    assert manifest["native_threads"]==[ids[0]]
    assert manifest["restore_policy"]=={"new_operations":False,"auto_reconcile":False,"worker_start":False,"identity_rebinding":False}
    assert manifest["sequences"]
    assert not (tmp_path/"bundle"/"native"/"auth.json").exists()
    assert not (tmp_path/"bundle"/"native"/"config.toml").exists()
    assert await fingerprints(factory)==before

async def test_legacy_reference_namespace_and_album_dependencies_are_included(factory,tmp_path):
    owner,_,_,roots,settings=await prepared(factory,tmp_path)
    roots["legacy_references"]=tmp_path/"refs"
    roots["legacy_generated"]=tmp_path/"generated"
    (roots["legacy_references"]/"private").mkdir(parents=True)
    (roots["legacy_references"]/"private"/"ref.png").write_bytes(png())
    (roots["legacy_generated"]/"seedream").mkdir(parents=True)
    (roots["legacy_generated"]/"seedream"/"old.png").write_bytes(png())
    async with factory.begin() as db:
        db.add(ReferenceAsset(user_id=owner,kind="hair",object_key="references/private/ref.png",
            file_name="ref.png",mime_type="image/png"))
        db.add(AlbumItem(user_id=owner,image_url="/api/generated/seedream/old.png"))
    await create(factory,settings,tmp_path/"bundle",roots)
    manifest=backup.verify_bundle(tmp_path/"bundle")
    assert "legacy_references/private/ref.png" in manifest["files"]
    assert "legacy_generated/seedream/old.png" in manifest["files"]

async def test_missing_storage_root_and_external_legacy_url_refuse_before_artifact(factory,tmp_path):
    owner,_,_,roots,settings=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        db.add(AlbumItem(user_id=owner,image_url="https://external.invalid/private.png"))
    with pytest.raises(BackupError,match="unmapped_legacy_url"):
        await create(factory,settings,tmp_path/"bundle",roots)
    assert not (tmp_path/"bundle").exists()

async def test_source_pause_and_active_worker_guards(factory,tmp_path):
    owner,_,branch,roots,settings=await prepared(factory,tmp_path)
    async with factory.begin() as db:await set_control(db,"global",False)
    with pytest.raises(BackupError,match="generation_must_be_paused"):
        await create(factory,settings,tmp_path/"bundle",roots)
    async with factory.begin() as db:await set_control(db,"global",True)
    # Submit while temporarily unpaused, then pause again with a live lease.
    async with factory.begin() as db:await set_control(db,"global",False)
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    async with factory.begin() as db:
        await set_control(db,"global",True)
        (await db.get(Operation,opid)).lease_expires_at=utc_now()+timedelta(minutes=5)
    with pytest.raises(BackupError,match="active_worker_lease"):
        await create(factory,settings,tmp_path/"bundle",roots)
    assert not (tmp_path/"bundle").exists()

async def test_runtime_files_and_unknown_operation_state_are_preserved(factory,tmp_path):
    owner,_,branch,roots,settings=await prepared(factory,tmp_path)
    async with factory.begin() as db:await set_control(db,"global",False)
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    async with factory.begin() as db:
        op=await db.get(Operation,opid);op.state="reconciling";op.upstream_status=None
        await set_control(db,"global",True)
    workspace=roots["runtime"]/opid
    workspace.mkdir()
    (workspace/"manifest.json").write_text('{"not":"backup-control"}',encoding="utf-8")
    (workspace/"INCOMPLETE").write_text("runtime file",encoding="utf-8")
    await create(factory,settings,tmp_path/"bundle",roots)
    manifest=backup.verify_bundle(tmp_path/"bundle")
    assert "runtime/"+opid+"/manifest.json" in manifest["files"]
    assert "runtime/"+opid+"/INCOMPLETE" in manifest["files"]
    async with factory() as db:assert (await db.get(Operation,opid)).state=="reconciling"

async def test_dump_failure_leaves_incomplete_artifact(factory,tmp_path):
    _,_,_,roots,settings=await prepared(factory,tmp_path)
    def failed(*args):raise BackupError("database_dump_failed")
    async with factory.begin() as db:
        with pytest.raises(BackupError,match="database_dump_failed"):
            await backup.create_bundle(db,settings,tmp_path/"bundle",roots,writers_stopped=True,dump=failed)
    assert (tmp_path/"bundle"/"INCOMPLETE").is_file()
    with pytest.raises(BackupError,match="incomplete_backup"):backup.verify_bundle(tmp_path/"bundle")

async def test_existing_destination_source_overlap_and_omitted_stop_are_refused(factory,tmp_path):
    _,_,_,roots,settings=await prepared(factory,tmp_path)
    (tmp_path/"bundle").mkdir()
    with pytest.raises(BackupError,match="new_absolute"):
        await create(factory,settings,tmp_path/"bundle",roots)
    with pytest.raises(BackupError,match="backup_source_overlap"):
        await create(factory,settings,roots["assets"]/"bundle",roots)
    async with factory.begin() as db:
        with pytest.raises(BackupError,match="writers_stopped"):
            await backup.create_bundle(db,settings,tmp_path/"new",roots,dump=fake_dump)

@pytest.mark.parametrize("change",["corrupt","extra","remove","manifest","incomplete"])
async def test_verifier_rejects_changed_bundle(factory,tmp_path,change):
    _,_,_,roots,settings=await prepared(factory,tmp_path)
    root=tmp_path/"bundle"
    result=await create(factory,settings,root,roots)
    if change=="corrupt":(root/"database.dump").write_bytes(b"PGDMP-changed")
    if change=="extra":(root/"unexpected.env").write_text("not allowed",encoding="utf-8")
    if change=="remove":(root/"database.dump").unlink()
    if change=="manifest":(root/"manifest.json").write_text("{}",encoding="utf-8")
    if change=="incomplete":(root/"INCOMPLETE").touch()
    with pytest.raises(BackupError):
        backup.verify_bundle(root,expected_manifest_sha256=result["manifest_sha256"])

def test_database_credentials_are_only_in_explicit_child_environment(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setenv("PGSERVICE","unrelated")
    monkeypatch.setenv("PGPASSWORD","unrelated")
    def run(command,**kwargs):
        calls.append((command,kwargs))
        if "--version" in command:return SimpleNamespace(returncode=0,stdout=b"pg_dump (PostgreSQL) 14.24\n")
        kwargs["stdout"].write(b"PGDMP-test")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess,"run",run)
    backup.dump_database("pg_dump","postgresql+asyncpg://user:synthetic-password@localhost/db",
                         "000001-000002-1",tmp_path/"database.dump",14)
    assert all("synthetic-password" not in str(command) for command,_ in calls)
    assert calls[0][1]["env"]["PGPASSWORD"]=="synthetic-password"
    assert "PGSERVICE" not in calls[0][1]["env"]
    assert calls[0][1]["env"]["PGPASSFILE"]==__import__("os").devnull

def test_thread_dependency_walk_keeps_json_boundaries():
    assert backup.thread_references({"text":"a thread_id string","deep":[{"threadId":"id"}]})=={"id"}
    with pytest.raises(BackupError):backup.thread_references({"thread_id":42})
