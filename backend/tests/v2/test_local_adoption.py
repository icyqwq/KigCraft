import asyncio
import copy
import io
from pathlib import Path
from uuid import uuid4
import pytest
from PIL import Image
from sqlalchemy import select, func
from app.core.config import get_settings
from app.design.models import (Asset, Branch, Design, EditorCheckpoint, EditorDraft, EditorSaveReceipt,
    Revision, Operation, Message, CodexEpoch, InputRecord, Quote, Wallet, LedgerEntry, DomainEvent)
from app.design.local_adoption import AdoptLocalImage, adopt_local
from .test_api import api
from .test_operations import setup
from .test_migrations import migration_database
from .test_editor_document_contract import CASES

async def upload(client, color, size=(16,16)):
    stream=io.BytesIO()
    Image.new("RGB",size,color).save(stream,format="PNG")
    response=await client.post("/api/v2/assets/uploads",files={"file":("image.png",stream.getvalue(),"image/png")})
    assert response.status_code==200,response.text
    return response.json()

async def payload(api):
    client,_,owner,_,_=api
    root=await upload(client,"coral")
    clean=await upload(client,"teal")
    reference=await upload(client,"gold",(8,8))
    doc=copy.deepcopy(CASES["valid"][0]["document"])
    doc.pop("baseRevision")
    doc["root"]={"assetId":root["id"],"sha256":root["sha256"],"width":16,"height":16}
    doc["normalizedRecipe"]["imageCoordinates"]={"version":1,"width":16,"height":16,"effectScale":1.0}
    doc["normalizedRecipe"]["face"]["faceWidth"]=.1
    doc["normalizedRecipe"]["annotations"]=[{"id":"pin-1","kind":"pin","x":.2,"y":.3,
        "width":.1,"height":.1,"endX":.4,"endY":.5,"note":""}]
    doc["annotationIntents"]=[{"annotationId":"pin-1","intent":"preserve","text":"Private annotation"}]
    doc["maskDocument"]={"coordinateSpace":"normalized-image","radiusUnit":"image-pixel",
        "strokes":[{"id":"mask-1","mode":"brush","points":[{"x":.3,"y":.4}],"radius":3.0}],
        "binding":{"sourceKey":root["id"],"width":16,"height":16,"geometry":"original-stale-binding"}}
    recipe=copy.deepcopy(doc["normalizedRecipe"])
    recipe["annotations"][0]["note"]="Private annotation"
    return {"client_request_id":str(uuid4()),"expected_owner_id":owner,"title":"Adopted local image","view":"front",
        "clean_asset_id":clean["id"],"document":doc,
        "draft":{"schema_version":2,"source_sha256":root["sha256"],"width":16,"height":16,
            "content":{"recipe":recipe,"maskStrokes":copy.deepcopy(doc["maskDocument"]["strokes"]),
                "maskBinding":copy.deepcopy(doc["maskDocument"]["binding"]),
                "localReferences":[{"key":"reference-1","assetId":reference["id"],"usage":"Keep the eyes",
                    "fileName":"reference.png","lastModified":123}],
                "localEditMode":"preserve","localEditNote":"Private local note","selectedLocalReferenceKeys":[],
                "localUploadedReferenceDescription":"","extraReferenceDescription":"Private extra"},
            "attachments":[{"slot":"extra","asset_id":reference["id"],"file_name":"extra.png","last_modified":456}]}}

async def totals(factory):
    async with factory() as db:
        return {model.__tablename__:await db.scalar(select(func.count()).select_from(model)) for model in
            (Asset,Design,Branch,Revision,EditorCheckpoint,EditorDraft,EditorSaveReceipt,
             Operation,Message,CodexEpoch,InputRecord,Quote,LedgerEntry,DomainEvent)}

async def test_adopt_is_atomic_private_idempotent_and_does_not_charge_or_generate(api):
    client,factory,owner,_,_=api
    body=await payload(api)
    before=await totals(factory)
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        balance=(wallet.balance,wallet.reserved,wallet.version)
    first=await client.post("/api/v2/local-images/adopt",json=body)
    assert first.status_code==200,first.text
    assert first.headers["cache-control"]=="private, no-store"
    result=first.json()
    assert (await client.post("/api/v2/local-images/adopt",json=body)).json()==result
    revision,checkpoint=result["revision"],result["checkpoint"]
    assert revision["source_kind"]=="local_import" and revision["parent_revision_id"] is None
    assert revision["operation_id"] is None and revision["can_continue"] and not revision["can_regenerate"]
    assert revision["root_source_asset_id"]==body["document"]["root"]["assetId"]
    assert revision["asset_id"]==body["clean_asset_id"] and revision["number"]==1
    assert checkpoint["document"]=={**body["document"],"baseRevision":revision["id"]}
    assert revision["recipe"]==body["draft"]["content"]["recipe"]
    async with factory() as db:
        drafts=(await db.scalars(select(EditorDraft))).all()
        assert len(drafts)==1 and drafts[0].document==body["draft"]
        assert drafts[0].base_revision_id==revision["id"] and drafts[0].root_asset_id==revision["root_source_asset_id"]
        branch=await db.get(Branch,revision["branch_id"])
        assert branch.active_epoch_id is None and branch.context_seed=={"kind":"fresh"}
        assert branch.state_version==1 and branch.current_revision_id==revision["id"]
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved,wallet.version)==balance
    after=await totals(factory)
    for table in ("v2_designs","v2_branches","v2_revisions","v2_editor_checkpoints","v2_editor_drafts","v2_editor_save_receipts"):
        assert after[table]==before[table]+1
    for table in ("v2_assets","v2_operations","v2_messages","v2_codex_epochs","v2_input_snapshots","v2_quotes","v2_ledger"):
        assert after[table]==before[table]
    assert (await client.get(revision["root_image_url"])).status_code==200
    client.cookies.clear()
    assert (await client.get(revision["image_url"])).status_code==401
    assert (await client.post("/api/v2/local-images/adopt",json=body)).status_code==401

@pytest.mark.parametrize("change",["owner","root-owner","reference-owner","missing-extra","hash","dimensions","mask","recipe","unknown-engine","thread"])
async def test_invalid_adoption_has_no_partial_design_or_wallet_mutation(api,change):
    client,factory,owner,_,_=api
    body=await payload(api)
    expected=400
    if change=="owner":
        body["expected_owner_id"]=str(uuid4());expected=404
    elif change in {"root-owner","reference-owner"}:
        other,_,_=await setup(factory)
        from app.assets.storage import upload_asset
        stream=io.BytesIO();Image.new("RGB",(16,16),"plum").save(stream,format="PNG")
        foreign=upload_asset(stream.getvalue(),other,Path(get_settings().v2_storage_dir))
        async with factory.begin() as db:db.add(foreign)
        if change=="root-owner":
            body["document"]["root"].update(assetId=foreign.id,sha256=foreign.sha256)
            body["document"]["maskDocument"]["binding"]["sourceKey"]=foreign.id
            body["draft"]["source_sha256"]=foreign.sha256
            body["draft"]["content"]["maskBinding"]["sourceKey"]=foreign.id
        else:body["draft"]["content"]["localReferences"][0]["assetId"]=foreign.id
        expected=404
    elif change=="missing-extra":body["draft"]["attachments"][0]["asset_id"]=str(uuid4());expected=404
    elif change=="hash":
        body["document"]["root"]["sha256"]="a"*64;body["draft"]["source_sha256"]="a"*64
    elif change=="dimensions":
        small=await upload(client,"blue",(8,8));body["clean_asset_id"]=small["id"]
    elif change=="mask":body["draft"]["content"]["maskBinding"]["geometry"]="changed";expected=422
    elif change=="recipe":body["draft"]["content"]["recipe"]["face"]["faceWidth"]=.2;expected=422
    elif change=="unknown-engine":body["document"]["engineVersion"]="future";expected=422
    elif change=="thread":body["upstream_thread_id"]="untrusted";expected=422
    before=await totals(factory)
    response=await client.post("/api/v2/local-images/adopt",json=body)
    assert response.status_code==expected,response.text
    assert await totals(factory)==before

async def test_lost_response_replay_does_not_restore_move_current_or_recreate_a_draft(api):
    client,factory,_,_,_=api
    body=await payload(api)
    first=(await client.post("/api/v2/local-images/adopt",json=body)).json()
    design_id=first["design"]["id"]
    async with factory.begin() as db:
        from app.core.time import utc_now
        design=await db.get(Design,design_id);design.deleted_at=utc_now();design.metadata_version+=1
        draft=await db.scalar(select(EditorDraft).where(EditorDraft.design_id==design_id))
        draft.deleted_at=utc_now();draft.version+=1
    before=await totals(factory)
    again=await client.post("/api/v2/local-images/adopt",json=body)
    assert again.status_code==200,again.text
    assert again.json()["design"]["deleted_at"] is not None
    assert again.json()["revision"]==first["revision"]
    assert again.json()["checkpoint"]==first["checkpoint"]
    assert await totals(factory)==before
    changed=copy.deepcopy(body);changed["title"]="Other title"
    conflict=await client.post("/api/v2/local-images/adopt",json=changed)
    assert conflict.status_code==409 and conflict.json()["code"]=="idempotency_conflict"
    async with factory() as db:
        draft=await db.scalar(select(EditorDraft).where(EditorDraft.design_id==design_id))
        assert draft.deleted_at is not None and draft.version==2

async def test_parallel_adoption_has_one_receipt(api):
    client,factory,owner,_,_=api
    body=AdoptLocalImage.model_validate(await payload(api))
    async def attempt():
        async with factory.begin() as db:
            return (await adopt_local(db,owner,body,storage_root=Path(get_settings().v2_storage_dir)))[2].id
    first,second=await asyncio.gather(attempt(),attempt())
    assert first==second
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Revision))==1
        assert await db.scalar(select(func.count()).select_from(EditorDraft))==1



@pytest.mark.parametrize("target",["root","clean","reference"])
async def test_changed_image_bytes_fail_before_any_design_is_created(api,target):
    client,factory,_,_,_=api
    body=await payload(api)
    ids={"root":body["document"]["root"]["assetId"],"clean":body["clean_asset_id"],
        "reference":body["draft"]["content"]["localReferences"][0]["assetId"]}
    async with factory() as db:
        asset=await db.get(Asset,ids[target])
    file=Path(get_settings().v2_storage_dir)/asset.object_key
    file.write_bytes(file.read_bytes()+b"changed")
    before=await totals(factory)
    response=await client.post("/api/v2/local-images/adopt",json=body)
    assert response.status_code==400,response.text
    assert response.json()["code"]=="input_asset_changed"
    assert await totals(factory)==before

async def test_late_failure_rolls_back_the_entire_adoption(api,monkeypatch):
    from app.design import local_adoption
    from app.design.service import ApplicationError
    client,factory,_,_,_=api
    body=await payload(api)
    before=await totals(factory)
    async def fail(*args,**kwargs):
        raise ApplicationError("injected_draft_failure")
    with monkeypatch.context() as patch:
        patch.setattr(local_adoption,"save_draft",fail)
        response=await client.post("/api/v2/local-images/adopt",json=body)
        assert response.status_code==400
    assert await totals(factory)==before
    recovered=await client.post("/api/v2/local-images/adopt",json=body)
    assert recovered.status_code==200,recovered.text

async def test_adoption_request_namespace_cannot_collide_with_normal_design_creation(api):
    client,factory,_,_,_=api
    body=await payload(api)
    response=await client.post("/api/v2/designs",json={"client_request_id":body["client_request_id"],"title":body["title"]})
    assert response.status_code==200,response.text
    before=await totals(factory)
    response=await client.post("/api/v2/local-images/adopt",json=body)
    assert response.status_code==409,response.text
    assert await totals(factory)==before

async def test_imported_image_enters_the_normal_quote_review_and_generation_pipeline(api,tmp_path):
    from app.operations.worker import LocalWorker
    from .test_worker import ScriptedRuntime
    client,factory,owner,_,_=api
    body=await payload(api)
    adopted=(await client.post("/api/v2/local-images/adopt",json=body)).json()
    revision=adopted["revision"]
    original=(await client.get(revision["image_url"])).content
    turn={"client_request_id":str(uuid4()),"intent":"generate_or_clarify",
        "text":"Change the expression","base_revision_id":revision["id"],"expected_branch_version":1}
    price=await client.post(f"/api/v2/branches/{revision['branch_id']}/quotes",json=turn)
    assert price.status_code==200,price.text
    response=await client.post(f"/api/v2/branches/{revision['branch_id']}/turns",json={**turn,"quote_id":price.json()["id"]})
    assert response.status_code==202,response.text
    settings=get_settings().model_copy(update={"v2_runtime_dir":str(tmp_path/"runtime")})
    worker=LocalWorker(factory,settings,runtime=ScriptedRuntime())
    await worker.run_once()
    status=await client.get(f"/api/v2/operations/{response.json()['id']}")
    assert status.json()["state"]=="succeeded",status.text
    async with factory() as db:
        saved=await db.get(Revision,revision["id"])
        assert saved.recipe==body["draft"]["content"]["recipe"]
        assert saved.root_source_asset_id==body["document"]["root"]["assetId"]
        assert await db.scalar(select(func.count()).select_from(Revision))==2
        assert await db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind=="settle"))==1
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(4,0)
    assert (await client.get(revision["image_url"])).content==original


async def test_zero_quota_and_disabled_generation_still_allow_explicit_cloud_adoption(api,monkeypatch):
    from app.quota.wallet import entry
    client,factory,owner,_,_=api
    body=await payload(api)
    async with factory.begin() as db:
        wallet=await db.get(Wallet,owner)
        await entry(db,wallet,key="test-zero",kind="adjustment",
            balance_delta=-wallet.balance,reserved_delta=0)
    monkeypatch.setenv("V2_NEW_OPERATIONS","false")
    get_settings.cache_clear()
    before=await totals(factory)
    response=await client.post("/api/v2/local-images/adopt",json=body)
    assert response.status_code==200,response.text
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(0,0)
    after=await totals(factory)
    assert after["v2_ledger"]==before["v2_ledger"] and after["v2_operations"]==before["v2_operations"]
    monkeypatch.setenv("V2_ENABLED","false")
    get_settings.cache_clear()
    disabled=await client.post("/api/v2/local-images/adopt",json=body)
    assert disabled.status_code==404,disabled.text
    assert await totals(factory)==after

async def test_invalid_reference_image_returns_a_client_error_without_partial_adoption(api):
    import hashlib
    client,factory,owner,_,_=api
    body=await payload(api)
    data=b"not an image"
    storage=Path(get_settings().v2_storage_dir)
    key=f"invalid-reference-{uuid4()}.png"
    (storage/key).write_bytes(data)
    invalid=Asset(id=str(uuid4()),owner_id=owner,object_key=key,sha256=hashlib.sha256(data).hexdigest(),
        width=16,height=16,mime_type="image/png",byte_size=len(data),kind="upload",status="ready")
    async with factory.begin() as db:db.add(invalid)
    body["draft"]["content"]["localReferences"][0]["assetId"]=invalid.id
    before=await totals(factory)
    response=await client.post("/api/v2/local-images/adopt",json=body)
    assert response.status_code==400,response.text
    assert response.json()["code"]=="invalid_image"
    assert await totals(factory)==before
