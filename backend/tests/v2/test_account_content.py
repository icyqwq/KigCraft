"""Closed-account legacy content cleanup in disposable databases; no real files removed."""
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import io
import json
import pytest
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from app.assets.storage import upload_asset
from app.db.models import AlbumItem, GenerationEvent, GenerationJob, GenerationOutput, JobStatus, Project, ProjectDraft, ReferenceAsset, User
from app.design.models import AccountContentRetirement, Asset, LegacyMaterial
from app.design.legacy_materials import list_materials, preserved_draft
from app.design.service import ApplicationError
from app.maintenance import account_content as module, design_content, trash_assets
from app.maintenance.orphan_assets import CleanupError
from .test_account_closure import prepared, inspect as closure_inspect, request, close
from .test_operations import factory
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints

PRIVATE="private legacy content fixture"
REASON="Verified account content removal"


async def fixture(factory,tmp_path, *, legacy_design_reference=False):
    owner,admin,design_id,cfg,now=await prepared(factory)
    root=tmp_path/"assets";root.mkdir()
    data=io.BytesIO();Image.new("RGB",(4,4),"red").save(data,format="PNG")
    async with factory.begin() as db:
        asset=upload_asset(data.getvalue(),owner,root)
        old=root/asset.object_key
        asset.kind="legacy_reference";asset.object_key=f"legacy/{asset.id}.jpg"
        path=root/asset.object_key;path.parent.mkdir(exist_ok=True);old.rename(path)
        db.add(asset)
        project=Project(user_id=owner,name=PRIVATE);db.add(project);await db.flush()
        ref=ReferenceAsset(user_id=owner,kind="character",object_key=f"references/{owner}/original.png",file_name=PRIVATE,mime_type="image/png")
        db.add(ref)
        job=GenerationJob(project_id=project.id,user_id=owner,status=JobStatus.failed,runtime_status="failed",
            prompt_payload={"prompt":PRIVATE,"asset_id":asset.id, **({"design_id":design_id} if legacy_design_reference else {})},phase_label=PRIVATE,error_message=PRIVATE,token_usage={"total_tokens":5})
        db.add(job);await db.flush();await db.flush()
        output=GenerationOutput(job_id=job.id,index=0,object_key=f"{job.id}/original.png",
            image_url=f"/api/generated/{job.id}/original.png",landmarks={"note":PRIVATE})
        db.add(output);await db.flush()
        db.add(GenerationEvent(job_id=job.id,message=PRIVATE,payload={"text":PRIVATE},type="error"))
        db.add(ProjectDraft(project_id=project.id,data={"text":PRIVATE,"asset":asset.id}))
        db.add(AlbumItem(user_id=owner,project_id=project.id,output_id=output.id,image_url=output.image_url,
            recipe={"text":PRIVATE},metadata_json={"name":PRIVATE}))
        material=LegacyMaterial(owner_id=owner,source_kind="reference",source_id=ref.id,
            source_digest="a"*64,source_summary={"record":{"file_name":PRIVATE},"project":{"name":PRIVATE},"thread_id":str(uuid4())},asset_id=asset.id)
        db.add(material)
        draft=LegacyMaterial(owner_id=owner,source_kind="draft",source_id=project.id,
            source_digest="b"*64,source_summary={"record":{"data":{"text":PRIVATE}},"project":{"name":PRIVATE}})
        db.add(draft);await db.flush()
        ids={"thread":material.source_summary["thread_id"],"project":project.id,"job":job.id,"reference":ref.id,"material":material.id,"draft":draft.id,"asset":asset.id}
    cfg.v2_storage_dir=str(root);cfg.v2_account_content_cleanup_enabled=True
    cfg.v2_design_content_cleanup_enabled=True;cfg.v2_trash_asset_cleanup_enabled=True
    original=tmp_path/"original.png";original.write_bytes(data.getvalue())
    batch=await request(factory,cfg,owner,admin,now,await closure_inspect(factory,cfg,owner))
    await close(factory,cfg,owner,admin,now+timedelta(days=3),batch["request_id"])
    async with factory.begin() as db:
        report=await design_content.inspect_designs(db,cfg,writers_stopped=True,now=now+timedelta(days=4))
        await design_content.scrub_designs(db,cfg,report["digest"],admin,REASON,writers_stopped=True,now=now+timedelta(days=4))
    return owner,admin,cfg,now+timedelta(days=5),ids,original


async def inspect(factory,cfg,owner):
    async with factory.begin() as db:return await module.inspect_account(db,cfg,owner,writers_stopped=True)


async def apply(factory,cfg,owner,admin,report,now):
    async with factory.begin() as db:return await module.scrub_account(db,cfg,owner,report["digest"],admin,REASON,writers_stopped=True,now=now)


async def test_removes_legacy_bodies_preserves_resources_finance_originals_and_hides_materials(factory,tmp_path):
    owner,admin,cfg,now,ids,original=await fixture(factory,tmp_path)
    before=await fingerprints(factory)
    report=await inspect(factory,cfg,owner)
    assert PRIVATE not in json.dumps(report)
    assert ids["asset"] in report["resources"]["asset_ids"]
    result=await apply(factory,cfg,owner,admin,report,now)
    assert await apply(factory,cfg,owner,admin,report,now)==result
    assert result["files_erased"] is False and original.exists()
    async with factory() as db:
        _,encoded,_=await module.inventory(db,owner)
        assert PRIVATE not in encoded
        assert (await db.get(GenerationJob,ids["job"])).token_usage=={"total_tokens":5}
        assert (await db.get(ReferenceAsset,ids["reference"])).object_key.startswith("references/")
        marker=await db.get(AccountContentRetirement,owner)
        assert marker.resources==report["resources"]
        assert (await list_materials(db,owner))["items"]==[]
        with pytest.raises(ApplicationError,match="not_found"):await preserved_draft(db,owner,ids["draft"])
        assert await list_materials(db,str(uuid4()))=={"items":[],"next_cursor":None}
    after=await fingerprints(factory)
    for table in ("v2_assets","v2_ledger","v2_wallets","v2_quotes","v2_legacy_imports"):
        assert before[table]==after[table]
    async with factory.begin() as db:
        assets=await trash_assets.inspect_assets(db,cfg,writers_stopped=True,now=now)
        assert ids["asset"] in {item["asset_id"] for item in assets["candidates"]}


async def test_foreign_content_reference_protects_whole_account(factory,tmp_path):
    owner,admin,cfg,now,ids,_=await fixture(factory,tmp_path)
    async with factory.begin() as db:
        foreign=User(email=str(uuid4())+"@example.com");db.add(foreign);await db.flush()
        project=Project(user_id=foreign.id,name="other");db.add(project);await db.flush()
        db.add(ProjectDraft(project_id=project.id,data={"unknown_source":ids["project"]}))
    before=await fingerprints(factory)
    with pytest.raises(CleanupError,match="shared_account_content"):await inspect(factory,cfg,owner)
    assert await fingerprints(factory)==before


async def test_foreign_asset_reference_protects_only_image_not_closed_text(factory,tmp_path):
    owner,admin,cfg,now,ids,_=await fixture(factory,tmp_path)
    async with factory.begin() as db:
        foreign=User(email=str(uuid4())+"@example.com");db.add(foreign);await db.flush()
        project=Project(user_id=foreign.id,name="other");db.add(project);await db.flush()
        db.add(ProjectDraft(project_id=project.id,data={"asset":ids["asset"]}))
    await apply(factory,cfg,owner,admin,await inspect(factory,cfg,owner),now)
    async with factory.begin() as db:
        report=await trash_assets.inspect_assets(db,cfg,writers_stopped=True,now=now)
        assert ids["asset"] not in {item["asset_id"] for item in report["candidates"]}


async def test_failure_rolls_back_data_marker_and_immutable_trigger(factory,tmp_path,monkeypatch):
    from app.db.schema_catalog import read_catalog
    owner,admin,cfg,now,ids,_=await fixture(factory,tmp_path)
    report=await inspect(factory,cfg,owner)
    before=await fingerprints(factory)
    async with factory() as db:catalog=await read_catalog(db)
    async def fail(*args,**kwargs):raise RuntimeError("synthetic receipt failure")
    monkeypatch.setattr(module.closure,"receipt",fail)
    async with factory.begin() as db:
        with pytest.raises(RuntimeError,match="synthetic"):
            await module.scrub_account(db,cfg,owner,report["digest"],admin,REASON,writers_stopped=True,now=now)
    assert await fingerprints(factory)==before
    async with factory() as db:assert await read_catalog(db)==catalog


@pytest.mark.parametrize("change",["content","policy","permission"])
async def test_changed_inventory_or_access_refuses_cleanup(factory,tmp_path,change):
    owner,admin,cfg,now,ids,_=await fixture(factory,tmp_path)
    report=await inspect(factory,cfg,owner)
    if change=="content":
        async with factory.begin() as db:(await db.get(Project,ids["project"])).name="changed"
    elif change=="policy":cfg.v2_account_content_cleanup_enabled=False
    else:admin=owner
    before=await fingerprints(factory)
    with pytest.raises(CleanupError):await apply(factory,cfg,owner,admin,report,now)
    assert await fingerprints(factory)==before


async def test_resource_tombstone_is_immutable(factory,tmp_path):
    owner,admin,cfg,now,ids,_=await fixture(factory,tmp_path)
    await apply(factory,cfg,owner,admin,await inspect(factory,cfg,owner),now)
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:
            await db.execute(update(AccountContentRetirement).where(AccountContentRetirement.owner_id==owner).values(resources={}))
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:
            await db.execute(update(LegacyMaterial).where(LegacyMaterial.id==ids["material"]).values(source_summary={"restore":"private"}))


async def test_closed_legacy_reference_does_not_deadlock_design_cleanup(factory,tmp_path):
    owner,admin,cfg,now,ids,_=await fixture(factory,tmp_path,legacy_design_reference=True)
    await apply(factory,cfg,owner,admin,await inspect(factory,cfg,owner),now)
    async with factory() as db:assert await db.get(AccountContentRetirement,owner) is not None


async def test_native_dependency_survives_scrub_and_is_not_protected_forever(factory,tmp_path):
    from app.maintenance.container_backup import owned_threads
    from app.maintenance.native_history import protected_threads
    owner,admin,cfg,now,ids,_=await fixture(factory,tmp_path)
    report=await inspect(factory,cfg,owner)
    assert {"thread_id":ids["thread"]} in report["resources"]["native_threads"]
    async with factory() as db:assert (await owned_threads(db,include_retired=True))[ids["thread"]]==owner
    await apply(factory,cfg,owner,admin,report,now)
    async with factory() as db:
        assert (await owned_threads(db,include_retired=True))[ids["thread"]]==owner
        assert await protected_threads(db,[ids["thread"]],now)==set()


async def test_account_must_be_closed_and_designs_retired(factory):
    owner,admin,design_id,cfg,now=await prepared(factory)
    cfg.v2_account_content_cleanup_enabled=True
    with pytest.raises(CleanupError,match="closed_account_required"):await inspect(factory,cfg,owner)
    result=await request(factory,cfg,owner,admin,now,await closure_inspect(factory,cfg,owner))
    with pytest.raises(CleanupError,match="closed_account_required"):await inspect(factory,cfg,owner)
    await close(factory,cfg,owner,admin,now+timedelta(days=3),result["request_id"])
    with pytest.raises(CleanupError,match="design_content_retirement_required"):await inspect(factory,cfg,owner)


async def test_legacy_mixed_case_identifier_reference_is_protected(factory,tmp_path):
    owner,admin,cfg,now,ids,_=await fixture(factory,tmp_path)
    key="AbC-Legacy-Reference"
    async with factory.begin() as db:
        db.add(ReferenceAsset(id=key,user_id=owner,kind="character",
            object_key=f"references/{owner}/case.png",file_name=PRIVATE,mime_type="image/png"))
        foreign=User(email=str(uuid4())+"@example.com");db.add(foreign);await db.flush()
        project=Project(user_id=foreign.id,name="other");db.add(project);await db.flush()
        db.add(ProjectDraft(project_id=project.id,data={"reference":key}))
    with pytest.raises(CleanupError,match="shared_account_content"):await inspect(factory,cfg,owner)


async def test_downgrade_cannot_discard_content_resource_marker(factory,tmp_path,migration_database):
    import os,subprocess,sys
    from sqlalchemy import text
    from .test_migrations import ROOT
    owner,admin,cfg,now,ids,_=await fixture(factory,tmp_path)
    await apply(factory,cfg,owner,admin,await inspect(factory,cfg,owner),now)
    result=subprocess.run([sys.executable,"-m","alembic","downgrade","v2_0026"],cwd=ROOT,
        env={**os.environ,"V2_DATABASE_URL":migration_database,"PYTHONIOENCODING":"utf-8"},
        capture_output=True,text=True,encoding="utf-8",timeout=30)
    assert result.returncode != 0 and "Refuse to discard account content retirement references" in result.stderr
    async with factory() as db:
        assert await db.get(AccountContentRetirement,owner) is not None
        assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
