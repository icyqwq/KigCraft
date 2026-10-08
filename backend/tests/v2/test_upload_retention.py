"""Backend-only retention checks against isolated PostgreSQL and temporary files."""
import asyncio
from datetime import timedelta
from pathlib import Path
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from app.auth.models import UserRole
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import User
from app.design.models import AdminAction, Asset, Design, InputRecord, LegacyMaterial, Message
from app.maintenance.orphan_assets import CleanupError
from app.maintenance.upload_retention import inspect_uploads, apply_uploads
from app.assets.storage import upload_in_transaction
from .test_migrations import migration_database
from .test_operations import factory, setup, quoted, submit_request
from .test_orphan_assets import png
from app.assets.storage import normalize_upload


def stored():
    """Bytes the service keeps for png(): uploads are re-encoded as one compact JPEG."""
    return normalize_upload(png())[0]
from .test_legacy_inventory import fingerprints

async def prepared(factory,tmp_path):
    owner,design,branch=await setup(factory)
    root=tmp_path/"assets"
    now=utc_now()
    async with factory.begin() as db:
        (await db.get(User,owner)).role=UserRole.admin
        asset=await upload_in_transaction(db,png(),owner,root)
        asset.created_at=now-timedelta(days=60)
        db.add(asset)
        await db.flush()
    settings=Settings(_env_file=None,v2_upload_retention_days=30,v2_expired_upload_grace_days=7)
    return owner,design,branch,asset,root,settings,now

async def inspect(factory,root,settings,action="expire",**kwargs):
    async with factory.begin() as db:
        return await inspect_uploads(db,root,settings,action,**kwargs)

async def apply(factory,root,settings,owner,report,**kwargs):
    async with factory.begin() as db:
        return await apply_uploads(db,root,settings,report["action"],report["digest"],owner,
                                   "Approved test policy",batch_id=report["batch_id"],**kwargs)

async def test_policy_disabled_without_mutation(factory,tmp_path):
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    settings.v2_upload_retention_days=0
    before=await fingerprints(factory)
    with pytest.raises(CleanupError,match="upload_retention_disabled"):
        await inspect(factory,root,settings)
    assert await fingerprints(factory)==before and (root/asset.object_key).is_file()

async def test_expire_restore_and_idempotent_receipts_preserve_bytes(factory,tmp_path):
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    before=await fingerprints(factory)
    report=await inspect(factory,root,settings,now=now)
    assert [row["asset_id"] for row in report["candidates"]]==[asset.id]
    assert await fingerprints(factory)==before
    result=await apply(factory,root,settings,owner,report,now=now)
    assert await apply(factory,root,settings,owner,report,now=now)==result
    async with factory() as db:
        assert (await db.get(Asset,asset.id)).status=="expired"
    assert (root/asset.object_key).read_bytes()==stored()
    restore=await inspect(factory,root,settings,"restore",batch_id=result["receipt_id"],now=now)
    assert (await apply(factory,root,settings,owner,restore,now=now))["count"]==1
    async with factory() as db:
        assert (await db.get(Asset,asset.id)).status=="ready"

async def test_purge_waits_for_grace_period_and_keeps_audit_tombstones(factory,tmp_path):
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    report=await inspect(factory,root,settings,now=now)
    receipt=await apply(factory,root,settings,owner,report,now=now)
    assert not (await inspect(factory,root,settings,"purge",now=now+timedelta(days=6)))["candidates"]
    report=await inspect(factory,root,settings,"purge",now=now+timedelta(days=8))
    assert (await apply(factory,root,settings,owner,report,now=now+timedelta(days=8)))["count"]==1
    assert not (root/asset.object_key).exists()
    async with factory() as db:
        assert (await db.get(Asset,asset.id)).status=="purged"
        assert (await db.get(AdminAction,receipt["receipt_id"])).result["asset_ids"]==[asset.id]
    assert not (await inspect(factory,root,settings,"restore",batch_id=receipt["receipt_id"]))["candidates"]

@pytest.mark.parametrize("reference",["snapshot","message","legacy_fk","trashed_snapshot","object_key"])
async def test_shared_and_historical_references_are_retained(factory,tmp_path,reference):
    owner,design,branch,asset,root,settings,now=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        if reference in {"snapshot","trashed_snapshot","object_key"}:
            db.add(InputRecord(branch_id=branch,digest="a"*64,
                snapshot={"nested":{"old_reference":asset.object_key if reference=="object_key" else asset.id}}))
            if reference=="trashed_snapshot":
                (await db.get(Design,design)).deleted_at=now-timedelta(days=50)
        elif reference=="message":
            db.add(Message(branch_id=branch,role="user",content_parts=[{"type":"image","asset_id":asset.id}]))
        else:
            db.add(LegacyMaterial(owner_id=owner,source_kind="reference",source_id=asset.id,
                source_digest="b"*64,source_summary={},asset_id=asset.id))
    report=await inspect(factory,root,settings,now=now)
    assert not report["candidates"] and report["retained"]==[{"asset_id":asset.id,"reason":"referenced"}]
    assert (root/asset.object_key).read_bytes()==stored()

async def test_new_reference_invalidates_reviewed_digest(factory,tmp_path):
    owner,_,branch,asset,root,settings,now=await prepared(factory,tmp_path)
    report=await inspect(factory,root,settings,now=now)
    async with factory.begin() as db:
        db.add(InputRecord(branch_id=branch,digest="c"*64,snapshot={"asset":asset.id}))
    with pytest.raises(CleanupError,match="inventory_changed"):
        await apply(factory,root,settings,owner,report,now=now)
    async with factory() as db:
        assert (await db.get(Asset,asset.id)).status=="ready"

async def test_existing_asset_reader_prevents_expiry(factory,tmp_path):
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    async with factory.begin() as reader:
        await reader.get(Asset,asset.id)
        with pytest.raises(DBAPIError):
            await inspect(factory,root,settings,now=now)
    assert (await inspect(factory,root,settings,now=now))["candidates"]

async def test_new_asset_reader_waits_until_expiry_commit(factory,tmp_path):
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    report=await inspect(factory,root,settings,now=now)
    started=asyncio.Event()
    async def reader():
        started.set()
        async with factory() as db:
            return (await db.get(Asset,asset.id)).status
    async with factory.begin() as db:
        await apply_uploads(db,root,settings,"expire",report["digest"],owner,"Approved test policy",now=now)
        task=asyncio.create_task(reader())
        await started.wait()
        await asyncio.sleep(.05)
        assert not task.done()
    assert await asyncio.wait_for(task,3)=="expired"

async def test_unconfirmed_operation_and_non_admin_refuse_expiry(factory,tmp_path):
    owner,_,branch,asset,root,settings,now=await prepared(factory,tmp_path)
    await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    report=await inspect(factory,root,settings,now=now)
    assert report["blocked"]=="operation_unconfirmed"
    with pytest.raises(CleanupError,match="operation_unconfirmed"):
        await apply(factory,root,settings,owner,report,now=now)
    async with factory.begin() as db:
        (await db.get(User,owner)).role=UserRole.user
    with pytest.raises(CleanupError,match="admin_required"):
        await apply(factory,root,settings,owner,report,now=now)
    assert (root/asset.object_key).exists()

async def test_failed_purge_commit_can_be_resumed_without_reexposing_missing_bytes(factory,tmp_path):
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    report=await inspect(factory,root,settings,now=now)
    expiry=await apply(factory,root,settings,owner,report,now=now)
    later=now+timedelta(days=8)
    report=await inspect(factory,root,settings,"purge",now=later)
    with pytest.raises(RuntimeError):
        async with factory.begin() as db:
            await apply_uploads(db,root,settings,"purge",report["digest"],owner,"Approved test policy",now=later)
            raise RuntimeError("simulated commit failure")
    async with factory() as db:
        assert (await db.get(Asset,asset.id)).status=="expired"
    assert not (root/asset.object_key).exists()
    with pytest.raises(CleanupError,match="retained_upload_missing"):
        await inspect(factory,root,settings,"restore",batch_id=expiry["receipt_id"],now=later)
    resumed=await inspect(factory,root,settings,"purge",now=later)
    assert resumed["candidates"][0]["present"] is False
    assert (await apply(factory,root,settings,owner,resumed,now=later))["count"]==1

async def test_unapproved_kinds_fresh_assets_and_corrupt_files_are_not_removed(factory,tmp_path):
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        current=await db.get(Asset,asset.id)
        current.kind="master"
    assert not (await inspect(factory,root,settings,now=now))["candidates"]
    async with factory.begin() as db:
        current=await db.get(Asset,asset.id)
        current.kind="reference"
        current.created_at=now
    assert not (await inspect(factory,root,settings,now=now))["candidates"]
    async with factory.begin() as db:
        (await db.get(Asset,asset.id)).created_at=now-timedelta(days=60)
    (root/asset.object_key).write_bytes(b"changed")
    with pytest.raises(CleanupError,match="upload_content_changed"):
        await inspect(factory,root,settings,now=now)
    assert (root/asset.object_key).read_bytes()==b"changed"

async def test_pagination_can_advance_past_protected_uploads(factory,tmp_path,monkeypatch):
    owner,_,branch,asset,root,settings,now=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        for _ in range(2):
            extra=await upload_in_transaction(db,png(),owner,root)
            extra.created_at=now-timedelta(days=60)
            db.add(extra)
    monkeypatch.setattr("app.maintenance.upload_retention.MAX_BATCH",2)
    first=await inspect(factory,root,settings,now=now)
    assert len(first["candidates"])==2 and first["next_after"]
    second=await inspect(factory,root,settings,now=now,after=first["next_after"])
    assert len(second["candidates"])==1 and second["next_after"] is None
    async with factory.begin() as db:
        for item in first["candidates"]:
            db.add(InputRecord(branch_id=branch,digest="d"*64,snapshot={"asset":item["asset_id"]}))
    first=await inspect(factory,root,settings,now=now)
    assert len(first["retained"])==2 and first["next_after"]
    assert (await apply(factory,root,settings,owner,second,now=now,after=first["next_after"]))["count"]==1

async def test_restore_renews_retention_and_old_batch_cannot_restore_new_expiry(factory,tmp_path):
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    first=await apply(factory,root,settings,owner,await inspect(factory,root,settings,now=now),now=now)
    restore=await inspect(factory,root,settings,"restore",batch_id=first["receipt_id"],now=now)
    await apply(factory,root,settings,owner,restore,now=now)
    assert (await inspect(factory,root,settings,now=now))["retained"]==[{"asset_id":asset.id,"reason":"restored_recently"}]
    later=now+timedelta(days=40)
    second=await apply(factory,root,settings,owner,await inspect(factory,root,settings,now=later),now=later)
    assert second["receipt_id"]!=first["receipt_id"]
    assert not (await inspect(factory,root,settings,"purge",now=later+timedelta(days=1)))["candidates"]
    old_restore=await inspect(factory,root,settings,"restore",batch_id=first["receipt_id"],now=later)
    assert not old_restore["candidates"] and old_restore["retained"][0]["reason"]=="different_expiry_batch"

async def test_hardlinked_object_is_not_expired(factory,tmp_path):
    import os
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    alias=tmp_path/"keep.png"
    os.link(root/asset.object_key,alias)
    with pytest.raises(CleanupError,match="linked_upload"):
        await inspect(factory,root,settings,now=now)
    assert alias.read_bytes()==stored() and (root/asset.object_key).read_bytes()==stored()


async def test_disabled_policy_still_allows_restoration(factory,tmp_path):
    owner,_,_,asset,root,settings,now=await prepared(factory,tmp_path)
    receipt=await apply(factory,root,settings,owner,await inspect(factory,root,settings,now=now),now=now)
    settings.v2_upload_retention_days=0
    report=await inspect(factory,root,settings,"restore",batch_id=receipt["receipt_id"],now=now)
    assert (await apply(factory,root,settings,owner,report,now=now))["count"]==1
    async with factory() as db:
        assert (await db.get(Asset,asset.id)).status=="ready"

async def test_operator_can_reduce_page_size_and_case_insensitive_paths_remain_protected(factory,tmp_path):
    owner,_,branch,asset,root,settings,now=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        db.add(InputRecord(branch_id=branch,digest="e"*64,snapshot={"path":asset.object_key.upper()}))
    report=await inspect(factory,root,settings,now=now,limit=1)
    assert report["limit"]==1 and report["retained"][0]["reason"]=="referenced"
    assert (await apply(factory,root,settings,owner,report,now=now,limit=1))["count"]==0
    for limit in [0,101,-1,True]:
        with pytest.raises(CleanupError,match="invalid_retention_limit"):
            await inspect(factory,root,settings,now=now,limit=limit)
