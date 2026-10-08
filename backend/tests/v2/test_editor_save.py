import asyncio
import copy
import io
from uuid import uuid4
import pytest
from PIL import Image, PngImagePlugin
from pydantic import ValidationError
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from app.contracts.v2 import ContinuationDraft
from app.assets.storage import upload_asset, asset_path
from app.design.editor_document import EditorDocument
from app.design.editor_save import SaveEditorDocument, save_editor, read_checkpoint
from app.design.models import Asset, Branch, Design, EditorCheckpoint, EditorSaveReceipt, Operation, Revision, Wallet
from app.design.service import ApplicationError
from .test_manual import base
from .test_operations import factory, setup
from .test_migrations import migration_database
from .test_api import api
from .test_editor_document_contract import CASES

def document(revision, root):
    return {"schemaVersion":1,"engineVersion":"kigcraft-pixi-1",
        "root":{"assetId":root.id,"sha256":root.sha256,"width":root.width,"height":root.height},
        "baseRevision":revision.id,"normalizedRecipe":{"imageCoordinates":{"version":1,"width":root.width,"height":root.height,"effectScale":1.0},
            **{key:copy.deepcopy(value) for key,value in CASES["valid"][0]["document"]["normalizedRecipe"].items() if key!="imageCoordinates"}},
        "calibrationRevision":"calibration-1","annotationIntents":[],
        "maskDocument":{"coordinateSpace":"normalized-image","radiusUnit":"image-pixel","strokes":[],"binding":None}}

async def fixture(factory, tmp_path):
    owner, branch, revision = await base(factory, tmp_path)
    async with factory.begin() as db:
        (await db.get(Operation, revision.operation_id)).state = "succeeded"
        root = await db.get(Asset, revision.root_source_asset_id)
    payload = SaveEditorDocument(client_request_id=str(uuid4()),expected_branch_version=1,
        clean_asset_id=revision.clean_asset_id,document=document(revision,root))
    return owner, branch, revision, root, payload

async def save(factory, owner, branch, payload, tmp_path):
    async with factory.begin() as db:
        return await save_editor(db,owner,branch,payload,storage_root=tmp_path)

async def test_same_pixels_preserve_image_branch_events_wallet_and_exact_retry(factory,tmp_path):
    owner,branch,revision,root,payload=await fixture(factory,tmp_path)
    async with factory() as db:
        design=await db.get(Design,revision.design_id)
        before=(design.event_sequence,design.updated_at,design.last_branch_id)
    kind,first,result=await save(factory,owner,branch,payload,tmp_path)
    assert kind=="checkpoint" and result.id==revision.id and first.clean_asset_id==revision.clean_asset_id
    assert (await save(factory,owner,branch,payload,tmp_path))[1].id==first.id
    again=payload.model_copy(update={"client_request_id":str(uuid4()),"expected_checkpoint_id":first.id})
    kind,second,_=await save(factory,owner,branch,again,tmp_path)
    assert kind=="unchanged" and second.id==first.id
    async with factory() as db:
        design=await db.get(Design,revision.design_id)
        assert (design.event_sequence,design.updated_at,design.last_branch_id)==before
        assert design.revision_counter == revision.number == result.number == 1
        assert (await db.get(Branch,branch)).state_version==1
        assert (await db.get(Wallet,owner)).balance==4
        assert (await db.scalar(select(func.count()).select_from(Revision)))==1
        assert (await db.scalar(select(func.count()).select_from(EditorCheckpoint)))==1
        assert (await db.scalar(select(func.count()).select_from(EditorSaveReceipt)))==2

async def test_annotations_masks_and_calibration_are_immutable_state_not_duplicate_images(factory,tmp_path):
    owner,branch,revision,root,payload=await fixture(factory,tmp_path)
    _,first,_=await save(factory,owner,branch,payload,tmp_path)
    value=payload.document.model_dump()
    value["normalizedRecipe"]["annotations"]=[{"id":"note-1","kind":"pin","x":.2,"y":.3,"width":.1,"height":.1,"endX":.4,"endY":.5,"note":""}]
    value["annotationIntents"]=[{"annotationId":"note-1","intent":"preserve","text":"Keep this detail"}]
    value["normalizedRecipe"]["landmarks"]=copy.deepcopy(CASES["valid"][-1]["document"]["normalizedRecipe"]["landmarks"])
    value["calibrationRevision"]="calibration-2"
    value["maskDocument"]={"coordinateSpace":"normalized-image","radiusUnit":"image-pixel","strokes":[{"id":"mask-1","mode":"brush","points":[{"x":.2,"y":.3}],"radius":.05}],
        "binding":{"sourceKey":root.id,"width":root.width,"height":root.height,"geometry":"original-geometry"}}
    changed=payload.model_copy(update={"document":EditorDocument.model_validate(value),"client_request_id":str(uuid4()),"expected_checkpoint_id":first.id})
    kind,second,result=await save(factory,owner,branch,changed,tmp_path)
    assert kind=="checkpoint" and second.version==2 and result.id==revision.id
    assert second.document["maskDocument"]["strokes"][0]["id"]=="mask-1"
    async with factory() as db:
        old=await db.get(EditorCheckpoint,first.id)
        assert old.document["annotationIntents"]==[]
        assert (await read_checkpoint(db,owner,branch,revision.id)).id==second.id
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:
            await db.execute(text("UPDATE v2_editor_checkpoints SET document_digest='bad' WHERE id=:id"),{"id":second.id})
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:
            await db.execute(text("DELETE FROM v2_editor_save_receipts WHERE checkpoint_id=:id"),{"id":second.id})

async def test_decoded_pixels_ignore_png_metadata_but_detect_single_pixel_change(factory,tmp_path):
    owner,branch,revision,root,payload=await fixture(factory,tmp_path)
    with Image.open(asset_path(root,tmp_path)) as source:
        image=source.copy()
    extra=PngImagePlugin.PngInfo();extra.add_text("author","Different encoding")
    stream=io.BytesIO();image.save(stream,format="PNG",pnginfo=extra,compress_level=0)
    # Preserve the differing file identity to ensure the comparison isn't merely sha256 equality.
    data=stream.getvalue()
    import hashlib
    asset=Asset(id=str(uuid4()),owner_id=owner,object_key="metadata.png",sha256=hashlib.sha256(data).hexdigest(),
        mime_type="image/png",kind="reference",width=image.width,height=image.height,byte_size=len(data),status="ready")
    (tmp_path/"metadata.png").write_bytes(data)
    async with factory.begin() as db: db.add(asset)
    _,first,_=await save(factory,owner,branch,payload.model_copy(update={"clean_asset_id":asset.id}),tmp_path)
    assert first.clean_asset_id==revision.clean_asset_id
    image=image.convert("RGBA");image.putpixel((0,0),(1,2,3,255));stream=io.BytesIO();image.save(stream,format="PNG")
    changed=upload_asset(stream.getvalue(),owner,tmp_path)
    async with factory.begin() as db: db.add(changed)
    changed_request=payload.model_copy(update={"client_request_id":str(uuid4()),"expected_checkpoint_id":first.id,"clean_asset_id":changed.id})
    kind,saved,result=await save(factory,owner,branch,changed_request,tmp_path)
    assert kind=="revision" and result.id!=revision.id and result.clean_asset_id==changed.id
    assert result.number == revision.number + 1 == 2
    assert saved.document["baseRevision"]==result.id and saved.version==1
    assert result.root_source_asset_id==root.id
    async with factory() as db:
        assert (await db.get(Branch,branch)).state_version==2
        assert (await db.get(Wallet,owner)).balance==4
    # Replaying after branch advancement returns the same committed result.
    assert (await save(factory,owner,branch,changed_request,tmp_path))[2].id==result.id

async def test_checkpoint_cas_concurrency_preserves_first_writer_and_requires_new_calibration_identity(factory,tmp_path):
    owner,branch,revision,root,payload=await fixture(factory,tmp_path)
    async def write(label):
        value=payload.document.model_dump();value["calibrationRevision"]=label
        try: return await save(factory,owner,branch,payload.model_copy(update={"client_request_id":str(uuid4()),"document":EditorDocument.model_validate(value)}),tmp_path)
        except ApplicationError as error:return error.code
    results=await asyncio.gather(write("one"),write("two"))
    assert results.count("editor_checkpoint_conflict")==1
    success=next(item for item in results if isinstance(item,tuple))[1]
    value=success.document.copy();value["normalizedRecipe"]={**value["normalizedRecipe"],"landmarks":copy.deepcopy(CASES["valid"][-1]["document"]["normalizedRecipe"]["landmarks"])}
    with pytest.raises(ApplicationError,match="calibration_revision_required"):
        await save(factory,owner,branch,payload.model_copy(update={"client_request_id":str(uuid4()),"expected_checkpoint_id":success.id,"document":EditorDocument.model_validate(value)}),tmp_path)

async def test_source_and_bytes_are_verified_and_foreign_access_rejected(factory,tmp_path):
    owner,branch,revision,root,payload=await fixture(factory,tmp_path)
    other,_,_=await setup(factory)
    with pytest.raises(ApplicationError,match="not_found"):
        async with factory() as db:await read_checkpoint(db,other,branch,revision.id)
    value=payload.document.model_dump();value["root"]["sha256"]="f"*64
    with pytest.raises(ApplicationError,match="editor_source_mismatch"):
        await save(factory,owner,branch,payload.model_copy(update={"document":EditorDocument.model_validate(value)}),tmp_path)
    asset_path(root,tmp_path).write_bytes(b"tampered")
    with pytest.raises(ApplicationError,match="input_asset_changed"):
        await save(factory,owner,branch,payload,tmp_path)
    async with factory() as db:assert await db.scalar(select(func.count()).select_from(EditorCheckpoint))==0

@pytest.mark.parametrize("change",[
    {"schemaVersion":True},{"schemaVersion":1.0},{"schemaVersion":2},{"engineVersion":"unknown"},
    {"annotationIntents":[{"annotationId":"missing","intent":"modify","text":"x"}]},
    {"normalizedRecipe":{"annotations":[]}},
    {"maskDocument":{"coordinateSpace":"viewport","strokes":[]}},
])
def test_invalid_editor_document_contract(change):
    from types import SimpleNamespace
    revision=SimpleNamespace(id="revision")
    root=SimpleNamespace(id="root",sha256="a"*64,width=32,height=32)
    value=document(revision,root);value.update(change)
    with pytest.raises(ValidationError):EditorDocument.model_validate(value)

async def test_historical_unchanged_save_does_not_allocate_branch_or_move_current(factory,tmp_path):
    owner,branch,revision,root,payload=await fixture(factory,tmp_path)
    stream=io.BytesIO();Image.new("RGB",(root.width,root.height),"blue").save(stream,format="PNG")
    changed=upload_asset(stream.getvalue(),owner,tmp_path)
    async with factory.begin() as db:db.add(changed)
    _,_,new_image=await save(factory,owner,branch,payload.model_copy(update={"clean_asset_id":changed.id}),tmp_path)
    historical=payload.model_copy(update={"client_request_id":str(uuid4()),"expected_branch_version":2,
        "continuation":ContinuationDraft(source_revision_id=revision.id)})
    kind,checkpoint,result=await save(factory,owner,branch,historical,tmp_path)
    assert kind=="checkpoint" and result.id==revision.id and checkpoint.branch_id==branch
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Branch))==1
        current=await db.get(Branch,branch)
        assert current.current_revision_id==new_image.id and current.state_version==2

async def test_active_generation_and_stale_image_version_cannot_save_checkpoint(factory,tmp_path):
    owner,branch,revision,root,payload=await fixture(factory,tmp_path)
    with pytest.raises(ApplicationError,match="state_conflict"):
        await save(factory,owner,branch,payload.model_copy(update={"expected_branch_version":0}),tmp_path)
    async with factory.begin() as db:(await db.get(Operation,revision.operation_id)).state="running"
    with pytest.raises(ApplicationError,match="design_busy"):await save(factory,owner,branch,payload,tmp_path)
    async with factory() as db:assert await db.scalar(select(func.count()).select_from(EditorCheckpoint))==0

async def test_editor_save_http_roundtrip_is_private_and_idempotent(api,tmp_path):
    client,factory,owner,design,branch=api
    stream=io.BytesIO();Image.new("RGB",(32,32),"coral").save(stream,format="PNG")
    from app.core.config import get_settings
    from pathlib import Path
    asset=upload_asset(stream.getvalue(),owner,Path(get_settings().v2_storage_dir))
    async with factory.begin() as db:
        db.add(asset);await db.flush()
        revision=Revision(design_id=design,branch_id=branch,clean_asset_id=asset.id,root_source_asset_id=asset.id,source_kind="manual",recipe={})
        db.add(revision);await db.flush()
        target=await db.get(Branch,branch);target.current_revision_id=revision.id;target.state_version=1
    payload=SaveEditorDocument(client_request_id=str(uuid4()),expected_branch_version=1,clean_asset_id=asset.id,document=document(revision,asset)).model_dump(mode="json")
    path=f"/api/v2/branches/{branch}/editor-saves"
    first=await client.post(path,json=payload)
    assert first.status_code==200,first.text
    assert first.headers["cache-control"]=="private, no-store"
    assert first.json()["disposition"]=="checkpoint" and first.json()["revision"]["id"]==revision.id
    replay=await client.post(path,json=payload);assert replay.json()==first.json()
    getpath=f"/api/v2/branches/{branch}/editor-checkpoint?base_revision_id={revision.id}"
    loaded=await client.get(getpath)
    assert loaded.status_code==200 and loaded.json()["checkpoint"]==first.json()["checkpoint"]
    assert loaded.headers["cache-control"]=="private, no-store"
    altered=copy.deepcopy(payload);altered["document"]["calibrationRevision"]="different"
    conflict=await client.post(path,json=altered)
    assert conflict.status_code>=400 and "idempotency_conflict" in conflict.text
    other,_,other_branch=await setup(factory)
    assert (await client.get(f"/api/v2/branches/{other_branch}/editor-checkpoint?base_revision_id={revision.id}")).status_code==404
    client.cookies.clear()
    assert (await client.get(getpath)).status_code==401
    assert (await client.post(path,json=payload)).status_code==401

async def test_foreign_clean_asset_and_receipt_reuse_are_rejected(factory,tmp_path):
    owner,branch,revision,root,payload=await fixture(factory,tmp_path)
    other,_,_=await setup(factory)
    data=asset_path(root,tmp_path).read_bytes();foreign=upload_asset(data,other,tmp_path)
    async with factory.begin() as db:db.add(foreign)
    with pytest.raises(ApplicationError,match="not_found"):
        await save(factory,owner,branch,payload.model_copy(update={"clean_asset_id":foreign.id}),tmp_path)
    await save(factory,owner,branch,payload,tmp_path)
    with pytest.raises(ApplicationError,match="idempotency_conflict"):
        await save(factory,owner,branch,payload.model_copy(update={"expected_branch_version":2}),tmp_path)
