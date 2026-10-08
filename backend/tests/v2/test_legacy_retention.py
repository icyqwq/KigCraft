from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from PIL import Image
from sqlalchemy import select
from app.admin.v2_controls import set_control
from app.auth.models import UserRole
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import User
from app.design.models import Asset,Design,LegacyMaterial,Revision
from app.design.service import create_design
from app.maintenance.trash_assets import inspect_assets,expire_assets,purge_assets,retired_asset
from app.maintenance.v2_migration import InventoryError
from .test_legacy_import import prepared,apply
from .test_legacy_inventory import scan,fingerprints
from .test_operations import factory
from .test_migrations import migration_database


async def imported(factory,tmp_path,format="PNG"):
    root,store=tmp_path/"original",tmp_path/"v2"
    ids,_=await prepared(factory,root)
    # Original storage keys remain historical; detected format controls the copy.
    for path in root.rglob("*.png"):
        Image.new("RGB",(16,12),"coral").save(path,format=format)
    report=await scan(factory,root,ids["owner"])
    result=await apply(factory,ids,report,root,store)
    now=utc_now()
    async with factory.begin() as db:
        (await db.get(User,ids["owner"])).role=UserRole.admin
        for identity in result["design_ids"]:
            design=await db.get(Design,identity)
            design.deleted_at=now-timedelta(days=10)
            design.trash_restore_until=now-timedelta(days=3)
        await set_control(db,"global",True)
    settings=Settings(_env_file=None,v2_enabled=True,app_env="test",v2_runtime_mode="local",
        v2_storage_dir=str(store),v2_trash_asset_cleanup_enabled=True,v2_trash_asset_grace_days=1)
    return ids,root,store,report,settings,now


async def retire(factory,owner,settings,now):
    async with factory.begin() as db:
        report=await inspect_assets(db,settings,writers_stopped=True,now=now)
    async with factory.begin() as db:
        result=await expire_assets(db,settings,report["digest"],owner,"Approved copy retirement",
                                   writers_stopped=True,now=now)
    return report,result["receipt_id"]


@pytest.mark.parametrize("format,extension",[("PNG",".png"),("JPEG",".jpg"),("WEBP",".webp")])
async def test_copied_legacy_formats_purge_without_touching_originals_or_reimporting(factory,tmp_path,format,extension):
    ids,root,store,report,settings,now=await imported(factory,tmp_path,format)
    original={path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob("*") if path.is_file()}
    before=await fingerprints(factory)
    inspected,batch=await retire(factory,ids["owner"],settings,now)
    assert len(inspected["candidates"])==2
    assert all(item["key"].endswith(extension) for item in inspected["candidates"])
    # Expired files still exist but replay must report their retired state.
    expired=await apply(factory,ids,report,root,store)
    assert sorted(expired["retired_assets"])==sorted(item["asset_id"] for item in inspected["candidates"])
    async with factory.begin() as db:
        await purge_assets(db,settings,batch,ids["owner"],"Approved copy retirement",
                           writers_stopped=True,now=now+timedelta(days=2))
    assert not any((store/item["key"]).exists() for item in inspected["candidates"])
    replay=await apply(factory,ids,report,root,store)
    assert replay["imported"]==0 and replay["replayed"]==5 and len(replay["retired_assets"])==2
    assert not any((store/item["key"]).exists() for item in inspected["candidates"])
    assert original=={path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob("*") if path.is_file()}
    after=await fingerprints(factory)
    assert {name:value for name,value in before.items() if name not in {"v2_assets","v2_admin_actions"}}=={
        name:value for name,value in after.items() if name not in {"v2_assets","v2_admin_actions"}}


async def test_live_design_using_legacy_copy_retains_it(factory,tmp_path):
    ids,root,store,report,settings,now=await imported(factory,tmp_path,"JPEG")
    async with factory.begin() as db:
        asset=await db.scalar(select(Asset).order_by(Asset.id).limit(1))
        design=await create_design(db,ids["owner"],str(uuid4()),"Still using image")
        db.add(Revision(design_id=design.id,branch_id=design.last_branch_id,clean_asset_id=asset.id,
            root_source_asset_id=asset.id,source_kind="manual"))
    async with factory.begin() as db:
        inspected=await inspect_assets(db,settings,writers_stopped=True,now=now)
    assert asset.id not in {item["asset_id"] for item in inspected["candidates"]}
    assert (store/asset.object_key).exists()


async def test_reference_library_keeps_migrated_image_even_after_design_expiry(factory,tmp_path):
    ids,root,store,report,settings,now=await imported(factory,tmp_path,"WEBP")
    async with factory.begin() as db:
        asset=await db.scalar(select(Asset).order_by(Asset.id).limit(1))
        db.add(LegacyMaterial(owner_id=ids["owner"],source_kind="reference",source_id=str(uuid4()),
            source_digest="a"*64,source_summary={"record":{"label":"retained library"}},asset_id=asset.id))
    async with factory.begin() as db:
        inspected=await inspect_assets(db,settings,writers_stopped=True,now=now)
    assert asset.id not in {item["asset_id"] for item in inspected["candidates"]}


async def test_missing_copy_without_retirement_receipt_still_refuses_replay(factory,tmp_path):
    ids,root,store,report,settings,now=await imported(factory,tmp_path)
    async with factory.begin() as db:
        asset=await db.scalar(select(Asset).order_by(Asset.id).limit(1))
        asset.status="purged"
    (store/asset.object_key).unlink()
    with pytest.raises(InventoryError,match="legacy_imported_asset_unavailable"):
        await apply(factory,ids,report,root,store)


async def test_receipt_must_be_committed_and_match_asset_contents(factory,tmp_path):
    ids,root,store,report,settings,now=await imported(factory,tmp_path)
    async with factory.begin() as db:
        inspected=await inspect_assets(db,settings,writers_stopped=True,now=now)
    async with factory.begin() as db:
        await expire_assets(db,settings,inspected["digest"],ids["owner"],"Approved copy retirement",
                            writers_stopped=True,now=now)
        asset=await db.get(Asset,inspected["candidates"][0]["asset_id"])
        assert not await retired_asset(db,asset)
    async with factory.begin() as db:
        asset=await db.get(Asset,asset.id)
        assert await retired_asset(db,asset)
        asset.sha256="b"*64
        assert not await retired_asset(db,asset)


async def test_unmapped_legacy_shaped_object_is_not_enough_to_authorize_purge(factory,tmp_path):
    ids,root,store,report,settings,now=await imported(factory,tmp_path)
    async with factory.begin() as db:
        original=await db.scalar(select(Asset).order_by(Asset.id).limit(1))
        identity=str(uuid4())
        asset=Asset(id=identity,owner_id=ids["owner"],object_key=f"legacy/{identity}.png",
            sha256=original.sha256,mime_type="image/png",kind="legacy",
            width=original.width,height=original.height,byte_size=original.byte_size,status="ready")
        db.add(asset)
        revision=await db.scalar(select(Revision).limit(1))
        db.add(Revision(design_id=revision.design_id,branch_id=revision.branch_id,
            clean_asset_id=identity,root_source_asset_id=identity,source_kind="manual"))
    async with factory.begin() as db:
        inspected=await inspect_assets(db,settings,writers_stopped=True,now=now)
    assert identity not in {item["asset_id"] for item in inspected["candidates"]}
