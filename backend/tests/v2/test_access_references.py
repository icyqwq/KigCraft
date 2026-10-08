"""Allowlisted reference extraction and API components; no E2E or model calls."""
import json
from pathlib import Path
from uuid import uuid4
import pytest
from starlette.requests import Request
from sqlalchemy import select
from app.admin.access_references import BODY_FIELDS, QUERY_FIELDS, extract_fields, request_references, IncompleteReferences
from app.admin.access_collector import AccessCollector, PREFIX, RUNNING, collection_health
from app.admin.access_observations import record_rejection, reference_queries
from app.assets.storage import upload_in_transaction
from app.core.config import Settings, get_settings
from app.core.time import utc_now
from app.db.models import AuditCounter
from app.design.models import Asset, Branch, Revision
from .test_api import api
from .test_operations import factory, setup, quoted
from .test_migrations import migration_database
from .test_orphan_assets import png
from .test_access_observations import counters
from .test_legacy_inventory import fingerprints
from .test_manual import base
from .test_local_adoption import payload as adoption_payload
from .test_drafts import request as draft_request

def make_request(method, route, body=None, query=b""):
    sent=False
    async def receive():
        nonlocal sent
        if sent:
            raise AssertionError("Body was consumed more than once")
        sent=True
        return {"type":"http.request","body":body or b"","more_body":False}
    return Request({"type":"http","method":method,"path":route,"headers":[],
                    "query_string":query},receive)

@pytest.mark.parametrize("route,value,expected",[
 ("/v2/branches/{branch_id}/quotes",{"attachments":[{"asset_id":"a","usage":"private"}],"prepared_edit":{"clean_asset_id":"b","root_source_asset_id":"c"},
   "continuation":{"boundary_message_id":"m"},"answer_to":{"operation_id":"o"}}, {("asset","a"),("asset","b"),("asset","c"),("message","m"),("operation","o")}),
 ("/v2/branches/{branch_id}/turns",{"quote_id":"q","base_revision_id":"r","annotation_asset_id":"a","mask_asset_id":"mask"}, {("quote","q"),("revision","r"),("asset","a"),("asset","mask")}),
 ("/v2/branches/{branch_id}/manual-revisions",{"clean_asset_id":"a","base_revision_id":"r"}, {("asset","a"),("revision","r")}),
 ("/v2/branches/{branch_id}/editor-saves",{"clean_asset_id":"a","document":{"baseRevision":"r","root":{"assetId":"root"}}}, {("asset","a"),("asset","root"),("revision","r")}),
 ("/v2/local-images/adopt",{"expected_owner_id":"u","clean_asset_id":"a","document":{"root":{"assetId":"r"}},
   "draft":{"attachments":[{"asset_id":"x"}],"content":{"localReferences":[{"assetId":"y"}]}}}, {("account","u"),("asset","a"),("asset","r"),("asset","x"),("asset","y")}),
])
async def test_known_body_fields_only(route,value,expected):
    value["text"]="private user text"
    value["recipe"]={"asset_id":"do-not-read","nested":{"quote_id":"secret"}}
    request=make_request("POST",route,json.dumps(value).encode())
    refs,complete=await request_references(request,route)
    assert set(refs)==expected and complete

async def test_draft_legacy_and_unified_refs_are_extracted_without_notes():
    value={"branch_id":"b","base_revision_id":"r","root_asset_id":"a",
           "document":{"attachments":[{"asset_id":"x","file_name":"private"}],
                       "content":{"localReferences":[{"assetId":"y","usage":"private"}],"recipe":{"asset_id":"ignore"}}}}
    refs,complete=await request_references(make_request("PUT","/",json.dumps(value).encode()),"/api/v2/editor-drafts/{draft_id}")
    assert set(refs)=={("branch","b"),("revision","r"),("asset","a"),("asset","x"),("asset","y")} and complete

async def test_upload_query_never_reads_multipart_and_unknown_routes_never_parse_body():
    request=make_request("POST","/",b"multipart-private-bytes",b"expected_owner_id=owner")
    refs,complete=await request_references(request,"/api/v2/assets/uploads")
    assert refs==[("account","owner")] and complete and not hasattr(request,"_body")
    request=make_request("POST","/",b"invalid private bytes")
    assert await request_references(request,"/v2/unknown")==([],True)
    assert not hasattr(request,"_body")

async def test_query_last_scalar_and_resource_types_are_explicit():
    request=make_request("GET","/",query=b"branch_id=ignored&branch_id=selected&asset_id=not-consumed")
    assert await request_references(request,"/v2/designs/{design_id}")==([("branch","selected")],True)
    assert len(reference_queries([("asset","a"),("asset","b"),("revision","r"),("account","u")]))==3

async def test_bounded_or_malformed_body_marks_gap_without_partial_zero(factory,monkeypatch):
    import app.admin.access_references as module
    route="/v2/branches/{branch_id}/quotes"
    for body in (b"{",json.dumps({"attachments":[{"asset_id":str(i)} for i in range(129)]}).encode()):
        assert await request_references(make_request("POST","/",body),route)==([],False)
    monkeypatch.setattr(module,"MAX_BODY_BYTES",1)
    assert await request_references(make_request("POST","/",b"{}"),route)==([],False)
    collector=AccessCollector(factory=factory)
    now=utc_now()
    await collector.pulse(now=now)
    await record_rejection("actor",route,{"branch_id":"missing"},factory=factory,collector=collector,complete=False,now=now)
    async with factory() as db:
        health=await collection_health(db,Settings(_env_file=None),now=now)
    assert health["gap_recorded"] and health["status"]=="incomplete"
    assert not any(key.endswith(":miss") for key in await counters(factory))

async def foreign_asset(factory):
    owner,_,_=await setup(factory)
    async with factory.begin() as db:
        asset=await upload_in_transaction(db,png(),owner,Path(get_settings().v2_storage_dir))
        db.add(asset)
        await db.flush()
    return owner,asset.id

async def assert_only_counts_changed(factory,before):
    after=await fingerprints(factory)
    assert {k:v for k,v in after.items() if k!="audit_counters"}=={
        k:v for k,v in before.items() if k!="audit_counters"}

@pytest.mark.parametrize("mode",["query_branch","attachment","quote","regenerate"])
async def test_foreign_and_missing_references_keep_identical_errors(api,mode):
    client,factory,owner,design,branch=api
    other,_,foreign_branch=await setup(factory)
    foreign=None
    method="GET"
    url=f"/api/v2/designs/{design}"
    body=None
    if mode=="query_branch":
        foreign=foreign_branch
    elif mode=="attachment":
        _,foreign=await foreign_asset(factory)
        method="POST";url=f"/api/v2/branches/{branch}/quotes"
        body={"client_request_id":"reference-count-001","intent":"generate_or_clarify","text":"private content","expected_branch_version":0}
    elif mode=="quote":
        foreign=(await quoted(factory,other,foreign_branch)).quote_id
        method="POST";url=f"/api/v2/branches/{branch}/turns"
        body={"client_request_id":"quote-count-001","intent":"generate_or_clarify","text":"coral wig","expected_branch_version":0}
    else:
        _,_,revision=await base(factory,Path(get_settings().v2_storage_dir))
        foreign=revision.id
        method="POST";url=f"/api/v2/branches/{branch}/quotes"
        body={"client_request_id":"regenerate-count-001","intent":"generate_or_clarify","text":"","expected_branch_version":0}
    async def send(identity):
        if mode=="query_branch":
            return await client.get(url,params={"branch_id":identity})
        values={**body}
        if mode=="attachment":values["attachments"]=[{"asset_id":identity,"usage":"private usage"}]
        elif mode=="quote":values["quote_id"]=identity
        else:values["regenerate_revision_id"]=identity
        return await client.post(url,json=values)
    before=await fingerprints(factory)
    denied,missing=await send(foreign),await send(str(uuid4()))
    expected=400 if mode=="quote" else 404
    assert denied.status_code==missing.status_code==expected,denied.text
    assert denied.content==missing.content and dict(denied.headers)==dict(missing.headers)
    values=await counters(factory)
    assert sum(v for k,v in values.items() if k.endswith(":cross"))==1
    assert sum(v for k,v in values.items() if k.endswith(":miss"))==1
    assert "private" not in repr(values) and foreign not in repr(values)
    await assert_only_counts_changed(factory,before)

@pytest.mark.parametrize("mode",["manual","draft","account_binding"])
async def test_editor_body_and_upload_binding_are_counted(api,mode):
    client,factory,owner,_,_=api
    other,foreign=await foreign_asset(factory)
    if mode=="account_binding":
        before=await fingerprints(factory)
        response=await client.post("/api/v2/assets/uploads",params={"expected_owner_id":other},
            files={"file":("ref.png",png(),"image/png")})
    else:
        adopted=await client.post("/api/v2/local-images/adopt",json=await adoption_payload(api))
        assert adopted.status_code==200,adopted.text
        revision_id=adopted.json()["revision"]["id"]
        async with factory() as db:
            revision=await db.get(Revision,revision_id)
            branch=await db.get(Branch,revision.branch_id)
            root=await db.get(Asset,revision.root_source_asset_id)
        before=await fingerprints(factory)
        if mode=="manual":
            response=await client.post(f"/api/v2/branches/{branch.id}/manual-revisions",json={
                "client_request_id":str(uuid4()),"base_revision_id":revision.id,
                "expected_branch_version":branch.state_version,"clean_asset_id":foreign,"recipe":{}})
        else:
            values=draft_request(branch.id,revision,root).model_dump(mode="json")
            values["document"]["attachments"]=[{"slot":"extra","asset_id":foreign,"file_name":"private.png","last_modified":1}]
            response=await client.put("/api/v2/editor-drafts/"+str(uuid4()),json=values)
    assert response.status_code==404,response.text
    assert sum(v for k,v in (await counters(factory)).items() if k.endswith(":cross"))==1
    await assert_only_counts_changed(factory,before)

@pytest.mark.parametrize("state",[2,-2,1,0])
async def test_old_collector_cannot_certify_new_reference_scope(factory,state):
    collector=AccessCollector(factory=factory)
    now=utc_now()
    await collector.pulse(now=now)
    async with factory.begin() as db:
        db.add(AuditCounter(key=PREFIX+uuid4().hex+":heartbeat",value=state,updated_at=now))
    async with factory() as db:
        value=await collection_health(db,Settings(_env_file=None),now=now)
    assert value["scope"]=="private_resource_rejections"
    assert value["status"]=="incomplete" and not value["inventory_complete"]
