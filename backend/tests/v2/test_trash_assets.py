from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.admin.v2_controls import set_control
from app.auth.models import UserRole
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import User,Project,ProjectDraft
from app.design.models import Asset,Design,EditorDraft,Operation,Revision,AdminAction
from app.design.service import create_design
from app.maintenance import trash_assets as cleanup
from app.maintenance.orphan_assets import CleanupError
from .test_manual import base
from .test_operations import factory
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints


async def prepared(factory,tmp_path):
    root=tmp_path/"assets"
    owner,branch,revision=await base(factory,root)
    now=utc_now()
    async with factory.begin() as db:
        (await db.get(User,owner)).role=UserRole.admin
        design=await db.get(Design,revision.design_id)
        design.deleted_at=now-timedelta(days=10)
        design.trash_restore_until=now-timedelta(days=3)
        await set_control(db,"global",True)
        asset=await db.get(Asset,revision.clean_asset_id)
    settings=Settings(_env_file=None,v2_enabled=True,app_env="test",v2_runtime_mode="local",
        v2_storage_dir=str(root),v2_trash_asset_cleanup_enabled=True,v2_trash_asset_grace_days=1)
    return owner,revision,asset,settings,now


async def inspect(factory,settings,now):
    async with factory.begin() as db:
        return await cleanup.inspect_assets(db,settings,writers_stopped=True,now=now)


async def expire(factory,settings,owner,now):
    report=await inspect(factory,settings,now)
    async with factory.begin() as db:
        result=await cleanup.expire_assets(db,settings,report["digest"],owner,"Approved trash policy",
                                         writers_stopped=True,now=now)
    return result["receipt_id"]


async def purge(factory,settings,owner,batch,now):
    async with factory.begin() as db:
        return await cleanup.purge_assets(db,settings,batch,owner,"Approved trash policy",
                                         writers_stopped=True,now=now)


async def shared_design(factory,owner,asset,now,kind,storage_root=None):
    async with factory.begin() as db:
        other=await create_design(db,owner,str(uuid4()),"Other design")
        base_asset=asset
        if kind=="draft":
            from app.assets.storage import upload_asset
            base_asset=upload_asset((Path(storage_root)/asset.object_key).read_bytes(),owner,Path(storage_root))
            db.add(base_asset)
            await db.flush()
        shared=Revision(design_id=other.id,branch_id=other.last_branch_id,
            clean_asset_id=base_asset.id,root_source_asset_id=base_asset.id,source_kind="manual")
        db.add(shared)
        await db.flush()
        if kind in {"unlimited","expired"}:
            other.deleted_at=now-timedelta(days=20)
            if kind=="expired":
                other.trash_restore_until=now-timedelta(days=2)
        if kind=="draft":
            db.add(EditorDraft(id=str(uuid4()),owner_id=owner,design_id=other.id,
                branch_id=other.last_branch_id,base_revision_id=shared.id,root_asset_id=base_asset.id,
                version=1,document={"references":[asset.id]},device_id="fixture"))
        return other.id


async def test_expire_and_purge_only_owned_image_bytes_preserve_business_history(factory,tmp_path):
    owner,revision,asset,settings,now=await prepared(factory,tmp_path)
    before=await fingerprints(factory)
    report=await inspect(factory,settings,now)
    assert [item["asset_id"] for item in report["candidates"]]==[asset.id]
    batch=await expire(factory,settings,owner,now)
    file=Path(settings.v2_storage_dir)/asset.object_key
    assert file.exists()
    async with factory() as db:
        assert (await db.get(Asset,asset.id)).status=="expired"
        assert (await db.get(Revision,revision.id)).clean_asset_id==asset.id
        from app.design.catalog import list_designs
        _,details,_=await list_designs(db,owner,status="trash")
        assert details[revision.design_id]["thumbnail_url"] is None
    with pytest.raises(CleanupError,match="grace_period"):
        await purge(factory,settings,owner,batch,now)
    result=await purge(factory,settings,owner,batch,now+timedelta(days=2))
    assert result["count"]==1 and not file.exists()
    assert await purge(factory,settings,owner,batch,now+timedelta(days=2))==result
    after=await fingerprints(factory)
    assert {k:v for k,v in before.items() if k not in {"v2_assets","v2_admin_actions"}}=={
        k:v for k,v in after.items() if k not in {"v2_assets","v2_admin_actions"}}


@pytest.mark.parametrize("kind",["active","unlimited","draft"])
async def test_another_design_or_draft_reference_keeps_shared_bytes(factory,tmp_path,kind):
    owner,revision,asset,settings,now=await prepared(factory,tmp_path)
    await shared_design(factory,owner,asset,now,kind,settings.v2_storage_dir)
    report=await inspect(factory,settings,now)
    assert not report["candidates"]
    assert {"asset_id":asset.id,"reason":"live_or_unknown_reference"} in report["retained"]
    assert (Path(settings.v2_storage_dir)/asset.object_key).exists()


async def test_all_referring_designs_must_have_elapsed_deadlines(factory,tmp_path):
    owner,revision,asset,settings,now=await prepared(factory,tmp_path)
    await shared_design(factory,owner,asset,now,"expired")
    report=await inspect(factory,settings,now)
    assert [item["asset_id"] for item in report["candidates"]]==[asset.id]


async def test_legacy_opaque_json_reference_is_conservatively_preserved(factory,tmp_path):
    owner,revision,asset,settings,now=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        project=Project(user_id=owner,name="Legacy")
        db.add(project)
        await db.flush()
        db.add(ProjectDraft(project_id=project.id,data={"image_url":"/api/v2/assets/"+asset.id}))
    assert not (await inspect(factory,settings,now))["candidates"]


async def test_new_reference_after_expiry_blocks_physical_purge(factory,tmp_path):
    owner,revision,asset,settings,now=await prepared(factory,tmp_path)
    batch=await expire(factory,settings,owner,now)
    # Simulate an external/old writer; even an expired asset is rechecked.
    await shared_design(factory,owner,asset,now,"active")
    with pytest.raises(CleanupError,match="trash_asset_referenced"):
        await purge(factory,settings,owner,batch,now+timedelta(days=2))
    assert (Path(settings.v2_storage_dir)/asset.object_key).exists()


async def test_unconfirmed_operation_and_disabled_policy_refuse_cleanup(factory,tmp_path):
    owner,revision,asset,settings,now=await prepared(factory,tmp_path)
    with pytest.raises(CleanupError,match="disabled"):
        await inspect(factory,settings.model_copy(update={"v2_trash_asset_cleanup_enabled":False}),now)
    async with factory.begin() as db:
        (await db.get(Operation,revision.operation_id)).upstream_status=None
    with pytest.raises(CleanupError,match="operation_unconfirmed"):
        await inspect(factory,settings,now)


async def test_changed_file_is_not_deleted_and_grace_cannot_be_shortened(factory,tmp_path):
    owner,revision,asset,settings,now=await prepared(factory,tmp_path)
    settings=settings.model_copy(update={"v2_trash_asset_grace_days":7})
    batch=await expire(factory,settings,owner,now)
    shorter=settings.model_copy(update={"v2_trash_asset_grace_days":1})
    with pytest.raises(CleanupError,match="grace_period"):
        await purge(factory,shorter,owner,batch,now+timedelta(days=2))
    file=Path(settings.v2_storage_dir)/asset.object_key
    file.write_bytes(b"replacement")
    with pytest.raises(CleanupError,match="content_changed"):
        await purge(factory,shorter,owner,batch,now+timedelta(days=8))
    assert file.read_bytes()==b"replacement"


async def test_expiry_must_commit_before_purge(factory,tmp_path):
    owner,revision,asset,settings,now=await prepared(factory,tmp_path)
    report=await inspect(factory,settings,now)
    with pytest.raises(CleanupError,match="expiry_commit_required"):
        async with factory.begin() as db:
            result=await cleanup.expire_assets(db,settings,report["digest"],owner,"Approved trash policy",
                                              writers_stopped=True,now=now)
            await cleanup.purge_assets(db,settings,result["receipt_id"],owner,"Approved trash policy",
                                       writers_stopped=True,now=now+timedelta(days=2))
    assert (Path(settings.v2_storage_dir)/asset.object_key).exists()


async def test_failure_after_unlink_keeps_committed_expiry_and_can_resume(factory,tmp_path,monkeypatch):
    owner,revision,asset,settings,now=await prepared(factory,tmp_path)
    batch=await expire(factory,settings,owner,now)
    async def failed(*args,**kwargs):
        raise RuntimeError("synthetic commit boundary")
    with monkeypatch.context() as scope:
        scope.setattr(cleanup,"receipt",failed)
        with pytest.raises(RuntimeError,match="synthetic"):
            await purge(factory,settings,owner,batch,now+timedelta(days=2))
    async with factory() as db:
        assert (await db.get(Asset,asset.id)).status=="expired"
        assert (await db.get(AdminAction,batch)).action=="expire_trash_assets"
    assert not (Path(settings.v2_storage_dir)/asset.object_key).exists()
    assert (await purge(factory,settings,owner,batch,now+timedelta(days=2)))["count"]==1
