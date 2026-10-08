"""A30: complete V2 ID-addressed GET inventory against real owners and PostgreSQL."""
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlencode
import pytest_asyncio
from sqlalchemy import select
from app.core.config import get_settings
from app.db.models import AuditCounter
from app.design.models import Revision, Asset,Message,LegacyMaterial
from app.design.drafts import save_draft
from app.design.editor_save import SaveEditorDocument,save_editor
from app.design.service import digest
from app.design.router import router
from .test_api import api
from .test_migrations import migration_database
from .test_operations import quoted,submit_request,request
from .test_worker import prepared
from .test_worker import ScriptedRuntime
from .test_drafts import request as draft_request
from .test_editor_save import document
from .test_account_sessions import seed
from .test_legacy_inventory import fingerprints

# Every ID-addressed GET in the V2 router must remain represented here.
READS=[
 ("/designs/{design_id}/lifecycle","/designs/{design}/lifecycle"),
 ("/branches/{branch_id}/history","/branches/{branch}/history"),
 ("/branches/{branch_id}/messages/{message_id}/edit","/branches/{branch}/messages/{message}/edit"),
 ("/branches/{branch_id}/messages","/branches/{branch}/messages"),
 ("/designs/{design_id}/revisions","/designs/{design}/revisions"),
 ("/designs/{design_id}/revisions/{revision_id}","/designs/{design}/revisions/{revision}"),
 ("/revisions/{revision_id}/candidates","/revisions/{revision}/candidates"),
 ("/designs/{design_id}","/designs/{design}"),
 ("/operations/{operation_id}/question","/operations/{question}/question"),
 ("/operations/{operation_id}","/operations/{operation}"),
 ("/assets/{asset_id}","/assets/{asset}"),
 ("/designs/{design_id}/events","/designs/{design}/events?after=2147483647"),
 ("/branches/{branch_id}/editor-drafts","/branches/{branch}/editor-drafts?base_revision_id={revision}"),
 ("/branches/{branch_id}/editor-checkpoint","/branches/{branch}/editor-checkpoint?base_revision_id={revision}"),
 ("/revisions/{revision_id}/legacy","/revisions/{revision}/legacy"),
 ("/designs/{design_id}/legacy","/designs/{design}/legacy"),
 ("/account/legacy-materials/{material_id}/draft","/account/legacy-materials/{material}/draft"),
]
def test_get_route_inventory_is_complete():
    actual={r.path.removeprefix("/v2") for r in router.routes if "GET" in r.methods and "{" in r.path}
    assert actual=={template for template,_ in READS}

@pytest_asyncio.fixture
async def pair(api,tmp_path,monkeypatch):
    client,factory,*_=api
    monkeypatch.setattr(get_settings(),"v2_storage_dir",str(tmp_path/"assets"))
    # Synthetic tenants must not reuse the fixture's otherwise constant runtime identity.
    async def identity(runtime,rpc):return digest(str(id(runtime)))
    monkeypatch.setattr(ScriptedRuntime,"identity",identity)
    records=[]
    for label in ["A","B"]:
        worker,runtime,owner,branch,opid=await prepared(factory,tmp_path)
        assert await worker.run_once()==opid
        async with factory() as db:
            revision=await db.scalar(select(Revision).where(Revision.operation_id==opid))
        runtime.mode="clarification"
        payload=await quoted(factory,owner,branch,request(client_request_id="private-question-"+label,
            text="Ask me one question before making an image",base_revision_id=revision.id,expected_branch_version=1))
        question=await submit_request(factory,owner,branch,payload)
        assert await worker.run_once()==question
        async with factory.begin() as db:
            root=await db.get(Asset,revision.root_source_asset_id)
            draft_id=str(uuid4());await save_draft(db,owner,draft_id,draft_request(branch,revision,root))
            value=document(revision,root)
            value["normalizedRecipe"]["annotations"]=[{"id":"mark","kind":"pin","x":.2,"y":.3,
                "width":.1,"height":.1,"endX":.4,"endY":.5,"note":""}]
            value["annotationIntents"]=[{"annotationId":"mark","intent":"instruction","text":"PRIVATE-INSTRUCTION-"+label}]
            saved=SaveEditorDocument(client_request_id=str(uuid4()),expected_branch_version=1,
                clean_asset_id=root.id,document=value)
            _,checkpoint,_=await save_editor(db,owner,branch,saved,storage_root=tmp_path/"assets")
            material=LegacyMaterial(owner_id=owner,source_kind="draft",source_id=str(uuid4()),
                source_digest=digest({"private":label}),source_summary={"record":{"data":{"private":label}}})
            db.add(material);await db.flush()
            message=await db.scalar(select(Message.id).where(Message.operation_id==revision.operation_id,Message.role=="user"))
            records.append(dict(owner=owner,design=revision.design_id,branch=branch,revision=revision.id,
                asset=root.id,operation=revision.operation_id,question=question,message=message,material=material.id,draft=draft_id,checkpoint=checkpoint.id))
        records[-1]["token"]=(await seed(factory,owner))[0]
    yield client,factory,records

async def test_every_private_read_owner_foreign_missing_and_unauthenticated(pair):
    client,factory,records=pair
    before=await fingerprints(factory)
    missing={key:str(uuid4()) for key in records[0]}
    for own,other in [records,records[::-1]]:
        client.cookies.clear();client.cookies.set(get_settings().session_cookie_name,own["token"])
        for template,path in READS:
            allowed=await client.get("/api/v2"+path.format(**own))
            assert allowed.status_code==200,(template,allowed.status_code,allowed.text[:300])
            foreign=await client.get("/api/v2"+path.format(**other))
            absent=await client.get("/api/v2"+path.format(**missing))
            assert foreign.status_code==absent.status_code==404,(template,foreign.status_code,absent.status_code)
            assert foreign.content==absent.content,template
            assert dict(foreign.headers)==dict(absent.headers),template
        client.cookies.clear()
        for template,path in READS:
            assert (await client.get("/api/v2"+path.format(**own))).status_code==401,template
    after=await fingerprints(factory)
    assert {k:v for k,v in after.items() if k!="audit_counters"} == {k:v for k,v in before.items() if k!="audit_counters"}
    async with factory() as db:
        counters=list(await db.scalars(select(AuditCounter)))
    assert counters and all(row.key.startswith("v2-access:") for row in counters)
    assert any(row.key.endswith(":cross") and row.value>0 for row in counters)

async def test_owned_parent_cannot_resolve_foreign_child_or_cursor(pair):
    client,factory,records=pair
    own,other=records
    client.cookies.clear();client.cookies.set(get_settings().session_cookie_name,own["token"])
    paths=[
      "/designs/{design}?branch_id={branch}",
      "/designs/{design}/revisions?branch_id={branch}",
      "/designs/{design}/revisions/{revision}",
      "/branches/{branch}/messages/{message}/edit",
      "/branches/{branch}/editor-drafts?base_revision_id={revision}",
      "/branches/{branch}/editor-checkpoint?base_revision_id={revision}",
    ]
    for path in paths:
        child="branch" if path.startswith("/designs") and "{branch}" in path else "message" if "{message}" in path else "revision"
        foreign={**own,child:other[child]};missing={**own,child:str(uuid4())}
        first=await client.get("/api/v2"+path.format(**foreign));second=await client.get("/api/v2"+path.format(**missing))
        assert first.status_code==second.status_code==404,(path,first.status_code,first.text[:200])
        assert first.content==second.content
    # Opaque continuation identifiers cannot select another owner's history.
    for path,key in [
        ("/branches/{branch}/history?message_id={message}","message"),
    ]:
        first=await client.get("/api/v2"+path.format(**{**own,key:other[key]}))
        second=await client.get("/api/v2"+path.format(**{**own,key:str(uuid4())}))
        assert first.status_code==second.status_code and first.status_code in [400,404]
        assert first.content==second.content

async def test_account_catalogs_contain_only_the_authenticated_owner(pair):
    client,_,records=pair
    for own,other in [records,records[::-1]]:
        client.cookies.clear();client.cookies.set(get_settings().session_cookie_name,own["token"])
        for path,key in [("/designs","design"),("/account/editor-drafts","draft"),("/account/legacy-materials?kind=all","material")]:
            response=await client.get("/api/v2"+path)
            assert response.status_code==200
            ids=[item["id"] for item in response.json()["items"]]
            assert own[key] in ids and other[key] not in ids,(path,ids)


async def test_valid_foreign_pagination_cursors_cannot_read_other_history(pair):
    client,_,records=pair
    own,other=records
    paths=["/branches/{branch}/history","/branches/{branch}/messages","/designs/{design}/revisions?scope=design"]
    for path in paths:
        client.cookies.clear();client.cookies.set(get_settings().session_cookie_name,other["token"])
        separator="&" if "?" in path else "?"
        response=await client.get("/api/v2"+path.format(**other)+separator+"limit=1")
        assert response.status_code==200
        cursor=response.json()["next_cursor"];assert cursor,path
        client.cookies.clear();client.cookies.set(get_settings().session_cookie_name,own["token"])
        own_page=await client.get("/api/v2"+path.format(**own)+separator+"limit=1")
        own_cursor=own_page.json()["next_cursor"];assert own_cursor,path
        allowed=await client.get("/api/v2"+path.format(**own)+separator+urlencode({"cursor":own_cursor}))
        assert allowed.status_code==200
        denied=await client.get("/api/v2"+path.format(**own)+separator+urlencode({"cursor":cursor}))
        assert denied.status_code in [400,404],(path,denied.status_code)
