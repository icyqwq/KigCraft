import asyncio
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from app.core.time import utc_now
from app.design.models import Asset, Branch, Design, Message, Revision, Wallet
from app.design.history import history_page
from app.contracts.v2 import ContinuationDraft
from app.design.manual import SaveManualRevision, save_manual
from .test_api import api
from .test_operations import factory, setup, request, quoted, submit_request
from .test_migrations import migration_database
from .test_worker import prepared
from .test_history_boundaries import next_turn, revision_for


async def seed_history(factory, owner, design, branch, rounds=100):
    ids=[]
    async with factory.begin() as db:
        asset=Asset(owner_id=owner,object_key=str(uuid4()),sha256="a"*64,mime_type="image/png",
            kind="reference",width=16,height=16,byte_size=10)
        db.add(asset);await db.flush()
        stamp=utc_now()
        for i in range(rounds):
            msg=Message(branch_id=branch,role="user",content_parts=[{"type":"text","text":f"request {i}"}],created_at=stamp)
            db.add(msg);await db.flush();ids.append(msg.id)
            image=Revision(design_id=design,branch_id=branch,clean_asset_id=asset.id,root_source_asset_id=asset.id,
                source_kind="manual",created_at=stamp)
            db.add(image);await db.flush();ids.append(image.id)
        (await db.get(Branch,branch)).current_revision_id=image.id
    return ids


async def test_hundred_rounds_fixed_window_no_duplicates_and_no_read_side_effects(api):
    client,factory,owner,design,branch=api
    expected=await seed_history(factory,owner,design,branch)
    async with factory() as db:
        d=await db.get(Design,design);b=await db.get(Branch,branch);w=await db.get(Wallet,owner)
        before=(d.event_sequence,d.updated_at,d.last_branch_id,b.state_version,w.balance,w.reserved)
    path=f"/api/v2/branches/{branch}/history"
    response=await client.get(path,params={"limit":17})
    assert response.status_code==200,response.text
    page=response.json()
    assert len(page["items"])==17 and page["watermark"]==200
    assert response.headers["cache-control"]=="private, no-store"
    # Inserts after the first page, even with old timestamps, cannot shift its older pages.
    async with factory.begin() as db:
        late=Message(branch_id=branch,role="user",content_parts=[{"type":"text","text":"new"}],
                     created_at=utc_now()-timedelta(days=100))
        db.add(late);await db.flush();late_id=late.id
    found=[]
    while True:
        found[0:0]=[item[item["type"]]["id"] for item in page["items"]]
        assert page["watermark"]==200 and not page["ancestry_incomplete"]
        if page["next_cursor"] is None:break
        response=await client.get(path,params={"limit":17,"cursor":page["next_cursor"]})
        assert response.status_code==200,response.text
        page=response.json()
    assert found==expected and len(set(found))==200
    latest=(await client.get(path)).json()
    assert latest["items"][-1]["message"]["id"]==late_id
    snapshot=(await client.get(f"/api/v2/designs/{design}")).json()
    assert len(snapshot["history"]["items"])==50
    assert snapshot["current_revision"]["id"]==expected[-1]
    messages=(await client.get(f"/api/v2/branches/{branch}/messages",params={"limit":100})).json()
    assert len(messages["items"])==100 and messages["next_cursor"]
    revisions=(await client.get(f"/api/v2/designs/{design}/revisions",params={"limit":100})).json()
    assert len(revisions["items"])==100 and revisions["next_cursor"] is None
    assert [r["number"] for r in revisions["items"]]==list(range(1,101))
    async with factory() as db:
        d=await db.get(Design,design);b=await db.get(Branch,branch);w=await db.get(Wallet,owner)
        assert (d.event_sequence,d.updated_at,d.last_branch_id,b.state_version,w.balance,w.reserved)==before

async def test_legacy_backfill_freezes_known_forks_and_keeps_original_rows(migration_database):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.db.models import User
    from .test_revision_numbers import migrate_to
    result=migrate_to(migration_database,"v2_0009")
    assert result.returncode==0,result.stderr
    engine=create_async_engine(migration_database)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with sessions.begin() as db:
            user=User(email=f"{uuid4()}@example.com");db.add(user);await db.flush()
            await db.execute(text("""INSERT INTO v2_designs
                (id,owner_id,client_request_id,request_digest,title,metadata_version,event_sequence,pinned,created_at,updated_at)
                VALUES ('d',:owner,'request','digest','Legacy',0,0,false,now(),now())"""),{"owner":user.id})
            await db.execute(text("""INSERT INTO v2_branches (id,design_id,state_version,created_at)
                VALUES ('root','d',2,now()),('continue','d',0,now()),('retry','d',0,now())"""))
            await db.execute(text("""INSERT INTO v2_assets
                (id,owner_id,object_key,sha256,mime_type,kind,width,height,byte_size,status,created_at)
                VALUES ('a',:owner,'a.png','digest','image/png','reference',16,16,10,'ready',now())"""),{"owner":user.id})
            await db.execute(text("""INSERT INTO v2_input_snapshots (id,branch_id,schema_version,digest,snapshot,created_at)
                VALUES ('input','root',1,'digest','{}',now())"""))
            for op,branch,kind in (("o1","root","generate"),("o2","root","image_edit"),("o3","retry","regenerate")):
                await db.execute(text("""INSERT INTO v2_operations
                    (id,owner_id,design_id,branch_id,client_request_id,request_digest,input_snapshot_id,kind,state,
                     expected_branch_version,reserved_units,settled_units,reservation_closed,fencing_token,cancel_requested,created_at,updated_at)
                    VALUES (:id,:owner,'d',:branch,:id,'digest','input',:kind,'succeeded',0,0,0,true,0,false,now(),now())"""),
                    {"id":op,"owner":user.id,"branch":branch,"kind":kind})
            for mid,op,role,second in (("m1","o1","user",1),("m2","o1","assistant",3),("m3","o2","user",4),("m4","o2","assistant",6)):
                await db.execute(text("""INSERT INTO v2_messages (id,branch_id,operation_id,role,content_parts,status,created_at)
                    VALUES (:id,'root',:op,:role,'[]','completed',:stamp)"""),
                    {"id":mid,"op":op,"role":role,"stamp":utc_now().replace(year=2026,month=1,day=1,hour=0,minute=0,second=second,microsecond=0)})
            for rid,op,second in (("r1","o1",2),("r2","o2",5)):
                await db.execute(text("""INSERT INTO v2_revisions
                    (id,design_id,branch_id,operation_id,clean_asset_id,root_source_asset_id,source_kind,view,created_at)
                    VALUES (:id,'d','root',:op,'a','a','ai','front',:stamp)"""),
                    {"id":rid,"op":op,"stamp":utc_now().replace(year=2026,month=1,day=1,hour=0,minute=0,second=second,microsecond=0)})
            await db.execute(text("UPDATE v2_branches SET parent_branch_id='root',origin_revision_id='r1' WHERE id='continue'"))
            await db.execute(text("UPDATE v2_branches SET parent_branch_id='root',origin_revision_id='r2' WHERE id='retry'"))
        async def snapshot():
            async with engine.connect() as db:
                return {name:[dict(r) for r in (await db.execute(text(f"SELECT * FROM {name} ORDER BY id"))).mappings()]
                        for name in ("v2_designs","v2_branches","v2_messages","v2_revisions")}
        before=await snapshot()
        result=migrate_to(migration_database,"head");assert result.returncode==0,result.stderr
        await engine.dispose()
        after=await snapshot()
        assert after["v2_designs"][0].pop("history_counter")==6
        assert {r["id"]:r.pop("history_parent_sequence") for r in after["v2_branches"]}=={"root":None,"continue":3,"retry":3}
        assert {r["id"]:r.pop("history_sequence") for r in after["v2_messages"]}=={"m1":1,"m2":3,"m3":4,"m4":6}
        assert {r["id"]:r.pop("history_sequence") for r in after["v2_revisions"]}=={"r1":2,"r2":5}
        assert all(row.pop("image_kind")=="head_shell" for row in after["v2_revisions"])  # added later, defaults for old versions
        # Columns added by later migrations (e.g. content retirement) start empty on old rows.
        for added in set(after["v2_designs"][0]) - set(before["v2_designs"][0]):
            assert after["v2_designs"][0][added] in (None, {}, [], False) or added in {"history_counter", "last_activity_at", "list_sequence"}, added
            if added not in {"history_counter", "last_activity_at", "list_sequence"}:
                after["v2_designs"][0].pop(added)
        assert after["v2_designs"][0].pop("last_activity_at")==before["v2_designs"][0]["created_at"]
        assert after["v2_designs"][0].pop("list_sequence")>0
        assert all(row.pop("search_text")=="" for row in after["v2_messages"])
        assert after==before
        for branch in ("continue","retry"):
            async with sessions() as db:
                page=await history_page(db,await db.get(Design,"d"),await db.get(Branch,branch))
                assert [r.id for _,r in page.records]==["m1","r1","m2"]
        with pytest.raises(DBAPIError,match="boundary is immutable"):
            async with engine.begin() as db:
                await db.execute(text("UPDATE v2_branches SET history_parent_sequence=6 WHERE id='continue'"))
        assert migrate_to(migration_database,"head").returncode==0
    finally:await engine.dispose()


async def test_cursor_scope_limits_and_foreign_branch_are_checked(api):
    client,factory,owner,design,branch=api
    await seed_history(factory,owner,design,branch,rounds=2)
    page=(await client.get(f"/api/v2/branches/{branch}/history",params={"limit":1})).json()
    token=page["next_cursor"]
    other_owner,other_design,other_branch=await setup(factory)
    for path in (f"/api/v2/branches/{other_branch}/history",f"/api/v2/branches/{other_branch}/messages",
                 f"/api/v2/designs/{other_design}/revisions",
                 f"/api/v2/designs/{design}/revisions?branch_id={other_branch}"):
        assert (await client.get(path)).status_code==404
    assert (await client.get(f"/api/v2/branches/{branch}/messages",params={"cursor":token})).status_code==400
    for token in ("bad!", "W10", "e30", "A"*513):
        assert (await client.get(f"/api/v2/branches/{branch}/history",params={"cursor":token})).status_code in {400,422}
    for limit in (0,101,-1):
        assert (await client.get(f"/api/v2/branches/{branch}/history",params={"limit":limit})).status_code==422


async def records(factory,design_id,branch_id):
    async with factory() as db:
        d=await db.get(Design,design_id);b=await db.get(Branch,branch_id)
        return await history_page(db,d,b,limit=100)


async def test_regeneration_inherits_before_original_request_and_continuation_includes_selected_turn(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path)
    await worker.run_once()
    first_image=await revision_for(factory,first)
    second,second_image=await next_turn(factory,worker,owner,branch,"second request","second-request")
    third,future=await next_turn(factory,worker,owner,branch,"future request","third-request")
    payload=request(text="",regenerate_revision_id=second_image.id,client_request_id="retry-second",expected_branch_version=3)
    retry=await submit_request(factory,owner,branch,await quoted(factory,owner,branch,payload))
    await worker.run_once()
    retry_image=await revision_for(factory,retry)
    page=await records(factory,first_image.design_id,retry_image.branch_id)
    message_ops=[m.operation_id for kind,m in page.records if kind=="message"]
    assert first in message_ops and retry in message_ops and second not in message_ops and third not in message_ops
    assert {r.id for kind,r in page.records if kind=="revision"}=={first_image.id,retry_image.id}
    # Continue the first image after later turns exist: include its completed turn, not the future.
    payload=request(text="child",client_request_id="continue-first",base_revision_id=first_image.id,
        expected_branch_version=3,continuation=ContinuationDraft(source_revision_id=first_image.id))
    child=await submit_request(factory,owner,branch,await quoted(factory,owner,branch,payload))
    await worker.run_once()
    child_image=await revision_for(factory,child)
    page=await records(factory,first_image.design_id,child_image.branch_id)
    assert {m.operation_id for kind,m in page.records if kind=="message"}=={first,child}
    assert {r.id for kind,r in page.records if kind=="revision"}=={first_image.id,child_image.id}


async def test_nested_manual_forks_keep_ancestor_cutoffs_and_unknown_boundary_is_explicit(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path)
    await worker.run_once();image=await revision_for(factory,first)
    await next_turn(factory,worker,owner,branch,"future","second-request")
    payload=SaveManualRevision(client_request_id="manual-first",base_revision_id=image.id,expected_branch_version=2,
        clean_asset_id=image.clean_asset_id,recipe={},continuation=ContinuationDraft(source_revision_id=image.id))
    async with factory.begin() as db:first_manual=await save_manual(db,owner,branch,payload)
    payload=SaveManualRevision(client_request_id="manual-second",base_revision_id=first_manual.id,expected_branch_version=1,
        clean_asset_id=image.clean_asset_id,recipe={"x":1})
    async with factory.begin() as db:second_manual=await save_manual(db,owner,first_manual.branch_id,payload)
    payload=SaveManualRevision(client_request_id="nested-fork",base_revision_id=first_manual.id,expected_branch_version=2,
        clean_asset_id=image.clean_asset_id,recipe={"x":2},continuation=ContinuationDraft(source_revision_id=first_manual.id))
    async with factory.begin() as db:nested=await save_manual(db,owner,first_manual.branch_id,payload)
    page=await records(factory,image.design_id,nested.branch_id)
    assert [r.id for kind,r in page.records if kind=="revision"]==[image.id,first_manual.id,nested.id]
    assert {m.operation_id for kind,m in page.records if kind=="message"}=={first}
    async with factory.begin() as db:
        unknown=Branch(design_id=image.design_id,parent_branch_id=branch)
        db.add(unknown);await db.flush()
    page=await records(factory,image.design_id,unknown.id)
    assert page.records==[] and page.ancestry_incomplete


async def test_sequence_is_immutable_and_shared_between_message_and_revision_writers(factory):
    owner,design,branch=await setup(factory)
    await seed_history(factory,owner,design,branch,rounds=1)
    async def write(i):
        async with factory.begin() as db:
            item=Message(branch_id=branch,role="user",content_parts=[])
            db.add(item);await db.flush()
            return item.id,item.history_sequence
    items=await asyncio.gather(*(write(i) for i in range(8)))
    assert sorted(seq for _,seq in items)==list(range(3,11))
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:
            await db.execute(text("UPDATE v2_messages SET history_sequence=99 WHERE id=:id"),{"id":items[0][0]})
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:
            db.add(Message(branch_id=branch,role="user",content_parts=[],history_sequence=100))
            await db.flush()
