import copy
import io
from pathlib import Path
from uuid import uuid4
from PIL import Image
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from app.assets.storage import upload_asset
from app.contracts.v2 import InputSnapshot, SnapshotReference
from app.conversation.bindings import annotation_bindings, public_numbered_parts, reference_bindings, user_content_parts
from app.core.config import get_settings
from app.design.models import Branch, Design, InputRecord, Message, Operation, Revision, Wallet
from .test_api import api
from .test_migrations import migration_database

def snapshot():
    refs=tuple(SnapshotReference(asset_id=name,sha256="a"*64,ordinal=index,role=role,usage=name)
               for index,(name,role) in enumerate([("base","base"),("a","reference"),("b","reference"),("c","reference"),("mask","mask")],1))
    return InputSnapshot(user_text="图一参考颜色，标注 2 保留",base_revision_id="revision",branch_version=1,references=refs,
        prepared_edit={"clean_asset_id":"base","root_source_asset_id":"base","root_sha256":"a"*64,"width":32,"height":32,
        "recipe":{"annotations":[{"id":"stable-a","note":"first"},{"id":"stable-b","note":"keep","text":"visible label"}]}})

@pytest.mark.parametrize("marks", [[{"id":"same"},{"id":"same"}],[{"note":"no identity"}],[{"id":1}],[{"id":"a","note":42}],{"a":"not a list"}])
def test_ambiguous_annotation_bindings_are_rejected_before_submission(marks):
    value=snapshot().model_dump(mode="json")
    value["prepared_edit"]["recipe"]["annotations"]=marks
    with pytest.raises(ValidationError):
        InputSnapshot.model_validate(value)


def test_numbers_exclude_transport_images_and_do_not_change_snapshot_or_digest():
    value=snapshot();original=copy.deepcopy(value.model_dump(mode="json"));digest=value.digest()
    context=value.working_context()
    assert [(r["asset_id"],r["ordinal"],r.get("reference_number")) for r in context["references"]]==[
        ("base",1,None),("a",2,1),("b",3,2),("c",4,3),("mask",5,None)]
    assert context["annotation_bindings"]==annotation_bindings(value)
    assert value.annotation_review()[0]["numbered_annotations"][1]["annotation_id"]=="stable-b"
    context["references"][1]["usage"]="later transport copy"
    context["prepared_edit"]["annotations"][0]["note"]="later transport copy"
    value.annotation_review()[0]["annotations"][0]["note"]="later review copy"
    assert value.model_dump(mode="json")==original and value.digest()==digest
    assert reference_bindings(value)[2]["number"]==3

def test_later_deletion_does_not_change_frozen_message_annotation_or_reference_numbers():
    value=snapshot();frozen=user_content_parts(value)
    later=value.model_copy(deep=True,update={"references":tuple(r for r in value.references if r.asset_id!="b")})
    later.prepared_edit.recipe["annotations"].pop(0)
    assert [(r["asset_id"],r["number"]) for r in reference_bindings(later)]==[("a",1),("c",2)]
    assert annotation_bindings(later)[0]["number"]==1
    assert frozen[1]["items"][-1]=={"asset_id":"c","usage":"c","number":3}
    assert frozen[2]["annotations"][1]["annotation_id"]=="stable-b"
    assert frozen[2]["annotations"][1]["number"]==2

def test_legacy_projection_uses_stored_order_without_mutating_message():
    legacy=[{"type":"references","items":[{"asset_id":"a","usage":"A"},{"asset_id":"c","usage":"C"}]}]
    original=copy.deepcopy(legacy)
    result=public_numbered_parts(legacy,snapshot())
    assert [r["number"] for r in result[0]["items"]]==[1,2]
    assert result[1]["annotations"][1]["text"]=="visible label"
    assert legacy==original

async def seed_assets(factory,owner,design,branch):
    assets=[]
    for colour in ("coral","blue","green","yellow"):
        image=io.BytesIO();Image.new("RGB",(32,32),colour).save(image,format="PNG")
        assets.append(upload_asset(image.getvalue(),owner,Path(get_settings().v2_storage_dir)))
    async with factory.begin() as db:
        db.add_all(assets);await db.flush()
        revision=Revision(design_id=design,branch_id=branch,clean_asset_id=assets[0].id,root_source_asset_id=assets[0].id,source_kind="manual")
        db.add(revision);await db.flush()
        target=await db.get(Branch,branch);target.current_revision_id=revision.id;target.state_version=1
    return assets,revision

async def test_http_quote_submission_and_refresh_keep_message_local_numbers(api):
    client,factory,owner,design,branch=api
    assets,revision=await seed_assets(factory,owner,design,branch)
    original_record=None
    for selected in ([assets[1],assets[2],assets[3]],[assets[1],assets[3]]):
        payload={"client_request_id":str(uuid4()),"intent":"generate_or_clarify","text":"图一参考颜色",
            "base_revision_id":revision.id,"expected_branch_version":1,
            "attachments":[{"asset_id":asset.id,"usage":"usage-"+str(index)} for index,asset in enumerate(selected,1)]}
        quote=await client.post(f"/api/v2/branches/{branch}/quotes",json=payload)
        assert quote.status_code==200,quote.text
        preview=quote.json()["request_preview"]
        assert [r["number"] for r in preview["references"]]==list(range(1,len(selected)+1))
        payload["quote_id"]=quote.json()["id"]
        response=await client.post(f"/api/v2/branches/{branch}/turns",json=payload)
        assert response.status_code==202,response.text
        operation_id=response.json()["id"]
        replay=await client.post(f"/api/v2/branches/{branch}/turns",json=payload)
        assert replay.json()["id"]==operation_id
        async with factory() as db:
            op=await db.get(Operation,operation_id);record=await db.get(InputRecord,op.input_snapshot_id)
            if original_record is None:original_record=(record.id,copy.deepcopy(record.snapshot),record.digest)
            assert [r["ordinal"] for r in record.snapshot["references"]]==list(range(1,len(selected)+2))
        cancelled=await client.post(f"/api/v2/operations/{operation_id}/cancel")
        assert cancelled.status_code==200,cancelled.text
    for _ in range(2):
        data=(await client.get(f"/api/v2/designs/{design}")).json()
        messages=[m for m in data["messages"] if m["role"]=="user"]
        assert len(messages)==2
        bindings=[next(p["items"] for p in m["content_parts"] if p["type"]=="references") for m in messages]
        assert [[(r["asset_id"],r["number"]) for r in group] for group in bindings]==[
            [(assets[1].id,1),(assets[2].id,2),(assets[3].id,3)],[(assets[1].id,1),(assets[3].id,2)]]
    async with factory() as db:
        record=await db.get(InputRecord,original_record[0])
        assert (record.snapshot,record.digest)==original_record[1:]
        wallet=await db.get(Wallet,owner);assert (wallet.balance,wallet.reserved)==(5,0)

async def test_http_recovers_old_annotation_labels_from_original_snapshot_without_writing(api):
    client,factory,owner,design,branch=api
    value=snapshot()
    async with factory.begin() as db:
        record=InputRecord(branch_id=branch,digest=value.digest(),snapshot=value.model_dump(mode="json"))
        db.add(record);await db.flush()
        op=Operation(owner_id=owner,design_id=design,branch_id=branch,client_request_id=str(uuid4()),request_digest="a"*64,
                     input_snapshot_id=record.id,kind="local_edit",state="succeeded",expected_branch_version=0,
                     reserved_units=0,settled_units=0,reservation_closed=True)
        db.add(op);await db.flush()
        legacy=Message(branch_id=branch,operation_id=op.id,role="user",content_parts=[{"type":"text","text":value.user_text}])
        db.add(legacy);await db.flush();identity=legacy.id
        design_row=await db.get(Design,design);before=(design_row.event_sequence,design_row.updated_at)
    data=(await client.get(f"/api/v2/designs/{design}")).json()
    parts=next(m["content_parts"] for m in data["messages"] if m["id"]==identity)
    assert parts[-1]["annotations"]==annotation_bindings(value)
    async with factory() as db:
        assert (await db.get(Message,identity)).content_parts==[{"type":"text","text":value.user_text}]
        design_row=await db.get(Design,design);assert (design_row.event_sequence,design_row.updated_at)==before
