import base64
import sys
import hashlib
import json
from uuid import uuid4
from pathlib import Path
import pytest
from sqlalchemy import select, func
from PIL import Image, ImageChops
from app.assets.storage import upload_asset
from app.codex_runtime.artifacts import stage_image
from app.contracts.v2 import InputSnapshot, SubmitTurn
from app.core.config import Settings, get_settings
from app.design.models import Asset, Branch, InputRecord, LedgerEntry, Operation, Revision, Wallet
from app.design.service import ApplicationError
from app.operations.service import quote, submit
from app.operations.publish import publish
from app.operations.worker import LocalWorker
from .test_operations import factory, setup, running
from .test_migrations import migration_database
from .test_worker import ScriptedRuntime
from .test_reconciliation import HistoryRpc, history
from .test_local_images import inputs, png

@pytest.fixture(autouse=True)
def local_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("V2_LOCAL_EDIT_ENABLED", "true")
    monkeypatch.setenv("V2_STORAGE_DIR", str(tmp_path/"assets"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

async def draft(factory, tmp_path, mode="lock_outside", *, identity=None):
    owner, design, branch = identity if identity is not None else await setup(factory)
    base, mask, output = inputs()
    storage = tmp_path/"assets"
    async with factory.begin() as db:
        root = upload_asset(png(base), owner, storage)
        selection = upload_asset(png(mask), owner, storage)
        # Separate private working master represents the editor's frozen source-sized export.
        clean = upload_asset(png(base), owner, storage)
        db.add_all([root, selection, clean]); await db.flush()
        revision = Revision(design_id=design, branch_id=branch, clean_asset_id=root.id,
            root_source_asset_id=root.id, source_kind="manual")
        db.add(revision); await db.flush()
        current = await db.get(Branch, branch)
        current.current_revision_id=revision.id; current.state_version=1
        request = SubmitTurn(client_request_id=str(uuid4()), intent="generate_or_clarify", text="Change selected region",
            base_revision_id=revision.id, expected_branch_version=1, mask_asset_id=selection.id,
            local_edit_mode=mode, quote_id="preview",
            prepared_edit={"clean_asset_id":clean.id,"root_source_asset_id":root.id,"root_sha256":root.sha256,
                "width":160,"height":160,"recipe":{"imageCoordinates":{"version":1,"width":160,"height":160,"effectScale":1},"annotations":[{"id":"pin","type":"pin","x":.5,"y":.5,"note":"keep rim"}]}})
    return owner, branch, revision, request, output

async def accepted(factory, tmp_path, monkeypatch, *, mode="lock_outside", runtime_mode="image", image=None):
    owner, branch, base, request, output = await draft(factory,tmp_path,mode)
    if image is not None: output=image
    monkeypatch.setattr(sys.modules[ScriptedRuntime.__module__], "png", lambda:base64.b64encode(png(output)).decode())
    async with factory.begin() as db:
        price=await quote(db,owner,branch,request)
        op=await submit(db,owner,branch,request.model_copy(update={"quote_id":price.id}))
        opid=op.id
    runtime=ScriptedRuntime(runtime_mode)
    settings=Settings(_env_file=None,app_env="test",v2_local_edit_enabled=True,v2_local_user_id=owner,
        v2_storage_dir=str(tmp_path/"assets"),v2_runtime_dir=str(tmp_path/"runtime"))
    worker=LocalWorker(factory,settings,runtime=runtime)
    return worker,runtime,owner,branch,base,opid,output

async def test_local_locked_worker_publishes_processed_png_and_settles_once(factory,tmp_path,monkeypatch):
    worker,runtime,owner,branch,base,opid,output=await accepted(factory,tmp_path,monkeypatch)
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert (op.kind,op.state,op.settled_units)==("local_edit","succeeded",1)
        assert op.staged_outputs["main_image"]["local_input_digest"]
        record=await db.get(InputRecord,op.input_snapshot_id)
        snapshot=InputSnapshot.model_validate(record.snapshot)
        assert snapshot.prepared_edit and snapshot.local_edit_mode=="lock_outside"
        assert next(ref.asset_id for ref in snapshot.references if ref.role=="base") != base.clean_asset_id
        result=(await db.execute(select(Revision).where(Revision.operation_id==opid))).scalar_one()
        asset=await db.get(Asset,result.clean_asset_id)
        actual=Image.open(tmp_path/"assets"/asset.object_key).convert("RGBA")
        original,mask,_=inputs()
        outside=ImageChops.invert(mask.getchannel("A").point(lambda v:255 if v else 0))
        for channel in ImageChops.difference(actual,original).split():
            assert ImageChops.multiply(channel,outside).getbbox() is None
        assert result.root_source_asset_id==asset.id  # AI result starts a new root.
        assert (await db.get(Wallet,owner)).balance==4
        assert (await db.get(Wallet,owner)).reserved==0
    assert sum(images and method=="turn/start" for images,method,_ in runtime.calls)==1
    review_call=next(params for images,method,params in runtime.calls if not images and method=="turn/start")
    assert "keep rim" in review_call["input"][0]["text"]
    main_call=next(params for images,method,params in runtime.calls if images and method=="turn/start")
    context=json.loads(next(item["text"].split("\n",1)[1] for item in main_call["input"]
        if item.get("type")=="text" and item["text"].startswith("Working snapshot")))
    assert "recipe" not in context["prepared_edit"]
    assert context["prepared_edit"]["annotations"][0]["note"]=="keep rim"

@pytest.mark.parametrize("mode,expected,balance", [
    ("lock_outside","failed",5), ("preserve","succeeded",4),
])
async def test_unaligned_output_fails_without_charging_only_in_locked_mode(factory,tmp_path,monkeypatch,mode,expected,balance):
    worker,_,owner,branch,base,opid,_=await accepted(factory,tmp_path,monkeypatch,mode=mode,image=Image.new("RGB",(160,160),"blue"))
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state==expected
        if expected=="failed":
            assert op.error_code=="local_alignment_unverified"
            assert not op.staged_outputs
            assert (await db.get(Branch,branch)).current_revision_id==base.id
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(balance,0)

async def test_local_quote_freezes_recipe_mode_mask_and_checks_sources(factory,tmp_path):
    owner,branch,base,request,_=await draft(factory,tmp_path)
    async with factory.begin() as db:
        price=await quote(db,owner,branch,request)
    with pytest.raises(ApplicationError,match="quote_invalid"):
        async with factory.begin() as db:
            await submit(db,owner,branch,request.model_copy(update={"quote_id":price.id,"local_edit_mode":"preserve"}))
    changed=request.prepared_edit.model_copy(update={"root_sha256":"0"*64})
    with pytest.raises(ApplicationError,match="prepared_source_mismatch"):
        async with factory.begin() as db:
            await quote(db,owner,branch,request.model_copy(update={"prepared_edit":changed}))
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,0)
        assert (await db.execute(select(func.count()).select_from(Operation))).scalar_one()==0

async def test_local_input_dimensions_rejected_before_reservation(factory,tmp_path):
    owner,branch,_,request,_=await draft(factory,tmp_path)
    async with factory.begin() as db:
        mask=upload_asset(png(Image.new("RGB",(80,80),"white")),owner,tmp_path/"assets")
        db.add(mask);await db.flush();mask_id=mask.id
    with pytest.raises(ApplicationError,match="local_mask_size_mismatch"):
        async with factory.begin() as db:
            await quote(db,owner,branch,request.model_copy(update={"mask_asset_id":mask_id}))
    async with factory() as db:
        assert (await db.get(Wallet,owner)).reserved==0

async def test_local_gate_remains_closed_by_default(factory,tmp_path,monkeypatch):
    owner,branch,_,request,_=await draft(factory,tmp_path)
    monkeypatch.setenv("V2_LOCAL_EDIT_ENABLED","false");get_settings.cache_clear()
    with pytest.raises(ApplicationError,match="operation_kind_not_enabled"):
        async with factory.begin() as db:await quote(db,owner,branch,request)

async def test_publish_rejects_native_unprocessed_locked_image(factory,tmp_path,monkeypatch):
    worker,_,owner,_,_,opid,output=await accepted(factory,tmp_path,monkeypatch)
    token=await running(factory,opid)
    item={"id":"raw","type":"imageGeneration","status":"completed","result":base64.b64encode(png(output)).decode()}
    raw=stage_image(item,runtime_id="runtime",thread_id="thread",turn_id="turn",output_root=tmp_path,staging_root=tmp_path/"staging")
    with pytest.raises(ApplicationError,match="unprocessed_local_output"):
        async with factory.begin() as db:
            await publish(db,opid,"worker",token,"main_image",raw,storage_root=tmp_path/"assets")

async def test_local_reconciliation_repeats_processing_not_model_call(factory,tmp_path,monkeypatch):
    worker,runtime,owner,_,_,opid,output=await accepted(factory,tmp_path,monkeypatch,runtime_mode="crash_after_image")
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="reconciling"
        response=history(opid,op.upstream_turn_id)
        response["thread"]["turns"][0]["items"][1]["result"]=base64.b64encode(png(output)).decode()
    calls=[]
    runtime.connection=lambda *args,**kwargs:HistoryRpc(response,calls)
    assert await worker.reconcile(opid)=="succeeded"
    assert calls==["thread/read"]
    async with factory() as db:
        assert (await db.get(Wallet,owner)).balance==4
        assert (await db.execute(select(func.count()).select_from(Revision).where(Revision.operation_id==opid))).scalar_one()==1

def test_old_snapshot_digest_remains_valid_after_additive_local_fields():
    old={"schema_version":1,"user_text":"old","base_revision_id":None,"branch_version":0,"references":[],
         "annotation_notes":[],"style_version":"physical-shell-v1","runtime_config_version":"codex-0.155-v1"}
    digest=hashlib.sha256(json.dumps(old,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    assert InputSnapshot.model_validate(old).digest()==digest

async def test_local_publication_retry_uses_processed_descriptor_without_new_model_call(factory,tmp_path,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    import app.operations.worker as worker_module
    worker,runtime,owner,_,_,opid,_=await accepted(factory,tmp_path,monkeypatch)
    original=worker_module.publish
    async def temporarily_unavailable(*args,**kwargs):
        raise SQLAlchemyError("simulated publication failure")
    monkeypatch.setattr(worker_module,"publish",temporarily_unavailable)
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="publishing" and op.staged_outputs["main_image"]["local_input_digest"]
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,1)
    monkeypatch.setattr(worker_module,"publish",original)
    assert await worker.retry_publish(opid)=="succeeded"
    assert sum(images and method=="turn/start" for images,method,_ in runtime.calls)==1
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(4,0)
        assert (await db.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind=="settle"))).scalar_one()==1

async def test_worker_disabled_local_gate_releases_queued_request_without_model_calls(factory,tmp_path,monkeypatch):
    worker,runtime,owner,_,_,opid,_=await accepted(factory,tmp_path,monkeypatch)
    worker.settings=worker.settings.model_copy(update={"v2_local_edit_enabled":False})
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="failed" and op.error_code=="operation_kind_not_enabled"
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,0)
    assert runtime.calls==[]


async def test_small_outside_displacement_releases_hold_without_publishing(factory, tmp_path, monkeypatch):
    base, _, output = inputs()
    # A 12x12 displaced patch is far outside the allowed 40x40 central region.
    # Its changed pixels were diluted below the former whole-image thresholds.
    output.paste(base.crop((128, 132, 140, 144)), (132, 132, 144, 144))
    worker, runtime, owner, branch, original, opid, _ = await accepted(
        factory, tmp_path, monkeypatch, image=output
    )
    assert await worker.run_once() == opid
    async with factory() as db:
        operation = await db.get(Operation, opid)
        assert operation.state == "failed"
        assert operation.error_code == "local_alignment_unverified"
        assert operation.upstream_status == "completed"
        assert operation.settled_units == 0 and operation.reservation_closed
        assert not operation.staged_outputs
        assert (await db.get(Branch, branch)).current_revision_id == original.id
        assert await db.scalar(select(func.count()).select_from(Revision).where(Revision.operation_id == opid)) == 0
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (5, 0)
        entries = (await db.scalars(select(LedgerEntry).where(LedgerEntry.operation_id == opid))).all()
        assert [entry.kind for entry in sorted(entries, key=lambda entry: entry.created_at)] == ["reserve", "release"]
    assert sum(images and method == "turn/start" for images, method, _ in runtime.calls) == 1
