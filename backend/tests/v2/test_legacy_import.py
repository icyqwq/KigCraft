import asyncio
import hashlib
from pathlib import Path
import pytest
from sqlalchemy import func,select,text
from app.core.time import utc_now
from app.db.models import User,GenerationJob,GenerationOutput,AlbumItem,JobStatus
from app.design.models import Asset,Branch,Design,LegacyImport,Revision,Message,Operation,LedgerEntry
from app.maintenance.legacy_import import apply_inventory
from app.maintenance.v2_migration import InventoryError
from .test_legacy_inventory import seed,scan,fingerprints
from .test_operations import factory
from .test_migrations import migration_database

async def prepared(factory,root):
    ids=await seed(factory,root)
    async with factory.begin() as db:
        owner=await db.get(User,ids["owner"]);owner.password_hash="test-placeholder";owner.email_verified_at=utc_now()
    report=await scan(factory,root,ids["owner"])
    return ids,report

async def apply(factory,ids,report,root,store):
    async with factory.begin() as db:
        return await apply_inventory(db,ids["owner"],asset_root=root,storage_root=store,expected_digest=report["inventory_digest"])

async def test_repeated_import_preserves_old_tables_no_fake_chat_model_or_wallet(factory,tmp_path):
    root=tmp_path/"old";store=tmp_path/"new"
    ids,report=await prepared(factory,root)
    before=await fingerprints(factory)
    first=await apply(factory,ids,report,root,store)
    after=await fingerprints(factory)
    second=await apply(factory,ids,report,root,store)
    assert first["imported"]==5 and first["replayed"]==0 and second["imported"]==0 and second["replayed"]==5
    assert await fingerprints(factory)==after
    assert all(after[name]==value for name,value in before.items() if not name.startswith("v2_"))
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Design))==1
        assert await db.scalar(select(func.count()).select_from(Revision))==2
        branches=list((await db.scalars(select(Branch))).all())
        assert len(branches)==2 and all(b.current_revision_id and b.parent_branch_id is None and b.active_epoch_id is None for b in branches)
        for model in (Message,Operation,LedgerEntry):
            assert await db.scalar(select(func.count()).select_from(model))==0
        outputs=list((await db.scalars(select(LegacyImport).where(LegacyImport.source_kind=="output"))).all())
        album=await db.scalar(select(LegacyImport).where(LegacyImport.source_kind=="album"))
        source=next(row for row in outputs if row.source_id==ids["output"])
        assert album.revision_id==source.revision_id and album.source_summary["original_recipe"]=={"eyeHeight":0.1}
        revisions=list((await db.scalars(select(Revision))).all())
        assert {r.view for r in revisions}=={"front","composite"}
        assert all(r.parent_revision_id is None and r.recipe is None and r.context_checkpoint is None and r.source_kind=="legacy" for r in revisions)
        for asset in (await db.scalars(select(Asset))).all():
            assert hashlib.sha256((store/asset.object_key).read_bytes()).hexdigest()==asset.sha256

@pytest.mark.parametrize("defect",["bytes","metadata","live_job","anonymous"])
async def test_unreviewed_change_or_undrained_work_never_imports(factory,tmp_path,defect):
    root=tmp_path/"old";store=tmp_path/"new"
    ids,report=await prepared(factory,root)
    async with factory.begin() as db:
        if defect=="bytes":
            output=await db.get(GenerationOutput,ids["output"]);(root/output.object_key).write_bytes(b"changed")
        elif defect=="metadata":(await db.get(AlbumItem,ids["album"])).recipe={"changed":True}
        elif defect=="live_job":(await db.get(GenerationJob,ids["job"])).status=JobStatus.running
        else:(await db.get(User,ids["owner"])).password_hash=None
    if defect=="live_job":report=await scan(factory,root,ids["owner"])
    with pytest.raises(InventoryError):
        await apply(factory,ids,report,root,store)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Design))==0
        assert await db.scalar(select(func.count()).select_from(LegacyImport))==0
    assert not store.exists()

async def test_concurrent_repeated_import_and_later_user_pointer_are_preserved(factory,tmp_path):
    root=tmp_path/"old";store=tmp_path/"new"
    ids,report=await prepared(factory,root)
    results=await asyncio.gather(apply(factory,ids,report,root,store),apply(factory,ids,report,root,store))
    assert sorted(result["imported"] for result in results)==[0,5]
    async with factory.begin() as db:
        branch=await db.scalar(select(Branch).order_by(Branch.id).limit(1))
        choices=list((await db.scalars(select(Revision))).all())
        branch.current_revision_id=next(r.id for r in choices if r.id!=branch.current_revision_id);branch.state_version+=1
        chosen=branch.current_revision_id;version=branch.state_version
    await apply(factory,ids,report,root,store)
    async with factory() as db:
        branch=await db.scalar(select(Branch).order_by(Branch.id).limit(1))
        assert branch.current_revision_id==chosen and branch.state_version==version

async def test_durable_mapping_cannot_be_deleted_changed_or_silently_rebound(factory,tmp_path):
    root=tmp_path/"old";store=tmp_path/"new"
    ids,report=await prepared(factory,root);await apply(factory,ids,report,root,store)
    for command in ("DELETE FROM v2_legacy_imports","UPDATE v2_legacy_imports SET source_digest=repeat('f',64)"):
        with pytest.raises(Exception,match="immutable"):
            async with factory.begin() as db:await db.execute(text(command))
    async with factory.begin() as db:(await db.get(GenerationJob,ids["job"])).provider="changed-provider"
    changed=await scan(factory,root,ids["owner"])
    with pytest.raises(InventoryError,match="legacy_source_changed_after_import"):
        await apply(factory,ids,changed,root,store)

async def test_missing_image_is_reported_and_can_be_imported_after_explicit_reinspection(factory,tmp_path):
    root=tmp_path/"old";store=tmp_path/"new"
    ids,report=await prepared(factory,root)
    async with factory() as db:output=await db.get(GenerationOutput,ids["output"])
    path=root/output.object_key;data=path.read_bytes();path.unlink()
    missing=await scan(factory,root,ids["owner"])
    result=await apply(factory,ids,missing,root,store)
    assert len(result["skipped"])==2
    path.write_bytes(data)
    restored=await scan(factory,root,ids["owner"])
    result=await apply(factory,ids,restored,root,store)
    assert result["imported"]==2 and not result["skipped"]


async def test_explicit_cli_apply_commits_and_replays_same_reviewed_inventory(factory,migration_database,tmp_path):
    import os,subprocess,sys,json
    from .test_migrations import ROOT
    root=tmp_path/"old";store=tmp_path/"new"
    ids,report=await prepared(factory,root)
    for index in range(2):
        target=tmp_path/f"apply-{index}.json"
        result=subprocess.run([sys.executable,"-m","app.maintenance.v2_migration","--owner-id",ids["owner"],
            "--asset-root",str(root),"--storage-root",str(store),"--expected-digest",report["inventory_digest"],
            "--apply","--report",str(target)],cwd=ROOT,env={**os.environ,"V2_DATABASE_URL":migration_database},
            capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode==0,result.stdout+result.stderr
        data=json.loads(target.read_text(encoding="utf-8"))
        assert data["imported"]==(5 if index==0 else 0)
        async with factory() as db:assert await db.scalar(select(func.count()).select_from(LegacyImport))==5


async def test_repeat_import_detects_corrupted_v2_copy_without_overwriting_it(factory,tmp_path):
    root=tmp_path/"old";store=tmp_path/"new"
    ids,report=await prepared(factory,root);await apply(factory,ids,report,root,store)
    async with factory() as db:asset=await db.scalar(select(Asset).limit(1))
    copied=store/asset.object_key;copied.write_bytes(b"corrupt-copy")
    with pytest.raises(InventoryError,match="legacy_imported_asset_changed"):
        await apply(factory,ids,report,root,store)
    assert copied.read_bytes()==b"corrupt-copy"


async def test_failed_receipt_reports_committed_outcome_and_same_inventory_replays(factory,migration_database,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.maintenance.v2_migration import run
    root=tmp_path/"old";store=tmp_path/"new"
    ids,report=await prepared(factory,root)
    target=tmp_path/"receipt.json"
    args=SimpleNamespace(owner_id=ids["owner"],asset_root=root,storage_root=store,expected_digest=report["inventory_digest"],
        public_prefix="/api/generated",apply=True,report=target)
    monkeypatch.setenv("V2_DATABASE_URL",migration_database)
    original=Path.open
    def unavailable(path,*args,**kwargs):
        if path==target and args and args[0]=="x":raise OSError("fixture disk failure")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"open",unavailable)
    with pytest.raises(InventoryError,match="report_write_failed_import_may_be_committed"):
        await run(args)
    async with factory() as db:assert await db.scalar(select(func.count()).select_from(LegacyImport))==5
    result=await apply(factory,ids,report,root,store)
    assert result["imported"]==0 and result["replayed"]==5
