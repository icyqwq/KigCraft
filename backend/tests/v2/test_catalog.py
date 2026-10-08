from uuid import uuid4
from datetime import timedelta
import pytest
from sqlalchemy import select, text
from app.core.time import utc_now
from app.design.catalog import list_designs
from app.design.lifecycle import UpdateDesign, update_design
from app.design.models import Branch, Design, Message, Operation, Wallet
from app.design.service import ApplicationError, create_design, event
from app.design.history import history_page
from .test_migrations import migration_database
from .test_operations import factory, setup, quoted, submit_request
from .test_worker import prepared
from .test_api import api


async def test_catalog_pages_more_than_hundred_and_invalidates_changed_order(factory):
    owner, first, branch=await setup(factory)
    async with factory.begin() as db:
        start=utc_now()
        for i in range(105):
            design=await create_design(db,owner,"catalog-"+str(i),f"Design {i}")
            design.last_activity_at=start+timedelta(seconds=i)
        await db.flush()
        page,_,cursor=await list_designs(db,owner,limit=17)
        seen=[d.id for d in page]
        while cursor:
            page,_,cursor=await list_designs(db,owner,limit=17,cursor=cursor)
            seen.extend(d.id for d in page)
        assert len(seen)==len(set(seen))==106
        page,_,cursor=await list_designs(db,owner,limit=2)
        oldest=await db.get(Design,first)
        await update_design(db,owner,first,UpdateDesign(expected_metadata_version=oldest.metadata_version,pinned=True))
    async with factory() as db:
        with pytest.raises(ApplicationError,match="design_list_changed"):
            await list_designs(db,owner,cursor=cursor)
        page,_,_=await list_designs(db,owner,limit=2)
        assert page[0].id==first
        with pytest.raises(ApplicationError,match="invalid_design_cursor"):
            await list_designs(db,owner,q="different",cursor=cursor)


async def test_search_only_public_content_and_locates_old_message(factory):
    owner,design_id,branch_id=await setup(factory)
    async with factory.begin() as db:
        target=Message(branch_id=branch_id,role="user",content_parts=[{"type":"text","text":"Need 100% coral_eyes"},
            {"type":"references","items":[{"asset_id":"a","usage":"front fringe"}]},
            {"type":"annotations","annotations":[{"note":"small smile","text":"caption"}]}])
        internal=Message(branch_id=branch_id,role="tool",content_parts=[{"type":"text","text":"secret-diagnostic"}])
        assistant=Message(branch_id=branch_id,role="assistant",content_parts=[{"type":"text","text":"Ready ![hidden](C:/private/search-secret.png)"}])
        db.add_all([target,internal,assistant]);await db.flush()
        for i in range(60):
            db.add(Message(branch_id=branch_id,role="user",content_parts=[{"type":"text","text":f"Later {i}"}]))
        await db.flush()
        for phrase in ("100% coral_eyes","front fringe","small smile","Ready"):
            rows,details,_=await list_designs(db,owner,q=phrase)
            assert [d.id for d in rows]==[design_id]
            assert details[design_id]["match"]["branch_id"]==branch_id
        for phrase in ("secret-diagnostic","search-secret","C:/private","coralXeyes"):
            assert not (await list_designs(db,owner,q=phrase))[0]
        design=await db.get(Design,design_id);branch=await db.get(Branch,branch_id)
        page=await history_page(db,design,branch,message_id=target.id)
        assert [(kind,m.id) for kind,m in page.records]==[("message",target.id)]
        assert "search-secret" not in assistant.search_text and internal.search_text==""
    foreign,_,foreign_branch=await setup(factory)
    async with factory() as db:
        assert not (await list_designs(db,foreign,q="coral_eyes"))[0]
        with pytest.raises(ApplicationError,match="not_found"):
            await history_page(db,await db.get(Design,design_id),await db.get(Branch,branch_id),message_id=str(uuid4()))


async def test_worker_status_does_not_reorder_and_archive_does_not_cancel(factory,tmp_path):
    worker,runtime,owner,branch_id,opid=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        branch=await db.get(Branch,branch_id);design=await db.get(Design,branch.design_id)
        activity=design.last_activity_at
        stamp=await db.scalar(select(Design.list_sequence).where(Design.id==design.id))
        await event(db,design.id,"operation.state_changed",{"operation_id":opid,"state":"reviewing"})
        await event(db,design.id,"quota.changed",{})
        await db.refresh(design)
        assert design.last_activity_at==activity and design.list_sequence==stamp
        await update_design(db,owner,design.id,UpdateDesign(expected_metadata_version=0,archived=True))
        assert (await db.get(Operation,opid)).state=="queued"
        assert (await db.get(Wallet,owner)).balance==4
        assert not (await list_designs(db,owner))[0]
        assert (await list_designs(db,owner,status="archived"))[0][0].id==design.id
    await worker.run_once()
    async with factory.begin() as db:
        operation=await db.get(Operation,opid);assert operation.state=="succeeded"
        assert not operation.cancel_requested
        assert (await db.get(Wallet,owner)).balance==4
        archived=await db.get(Design,design.id)
        assert archived.archived_at and archived.last_activity_at>activity
        with pytest.raises(ApplicationError,match="metadata_conflict"):
            await update_design(db,owner,design.id,UpdateDesign(expected_metadata_version=0,archived=False))
        await update_design(db,owner,design.id,UpdateDesign(expected_metadata_version=1,archived=False))
        assert not archived.archived_at
        assert (await list_designs(db,owner))[0][0].id==design.id


async def test_http_metadata_conditional_private_catalog_and_message_location(api):
    client,factory,owner,design,branch=api
    async with factory.begin() as db:
        msg=Message(branch_id=branch,role="user",content_parts=[{"type":"text","text":"unique search phrase"}])
        db.add(msg);await db.flush()
    response=await client.get("/api/v2/designs?q=unique")
    assert response.status_code==200 and response.headers["cache-control"]=="private, no-store"
    assert response.json()["items"][0]["match"]["message_id"]==msg.id
    located=await client.get(f"/api/v2/branches/{branch}/history?message_id={msg.id}")
    assert located.status_code==200 and located.json()["items"][0]["message"]["id"]==msg.id
    path=f"/api/v2/designs/{design}"
    assert (await client.patch(path,json={"pinned":True,"expected_metadata_version":0})).status_code==200
    assert (await client.patch(path,json={"archived":True,"expected_metadata_version":0})).status_code==409
    assert (await client.patch(path,json={"archived":True,"expected_metadata_version":1})).status_code==200
    assert (await client.get("/api/v2/designs")).json()["items"]==[]
    assert (await client.get("/api/v2/designs?status=archived")).json()["items"][0]["id"]==design
    assert (await client.get(path)).status_code==200
    for changes in ({"expected_metadata_version":2},{"title":None,"expected_metadata_version":2},{"deleted_at":"now","expected_metadata_version":2},{"pinned":1,"expected_metadata_version":2}):
        assert (await client.patch(path,json=changes)).status_code==422
    _,foreign,_=await setup(factory)
    assert (await client.patch(f"/api/v2/designs/{foreign}",json={"archived":True,"expected_metadata_version":0})).status_code==404
    assert (await client.patch(path,json={"archived":False,"expected_metadata_version":2})).status_code==200
    # An unchanged request at the current version produces no new metadata event.
    before=(await client.get(path)).json()
    assert (await client.patch(path,json={"archived":False,"expected_metadata_version":3})).status_code==200
    assert (await client.get(path)).json()==before

async def test_catalog_cursor_detects_reverse_commit_order(factory):
    owner, first, _ = await setup(factory)
    async with factory.begin() as db:
        second=await create_design(db,owner,"second-design","Second")
        second_id=second.id
    async with factory() as earlier, factory() as later:
        await earlier.begin();await later.begin()
        try:
            await update_design(earlier,owner,first,UpdateDesign(expected_metadata_version=0,pinned=True))
            await update_design(later,owner,second_id,UpdateDesign(expected_metadata_version=0,pinned=True))
            await later.commit()
            async with factory() as db:
                _,_,cursor=await list_designs(db,owner,limit=1)
                assert cursor
            # Lower allocated sequence becomes visible after the higher one.
            await earlier.commit()
            async with factory() as db:
                with pytest.raises(ApplicationError,match="design_list_changed"):
                    await list_designs(db,owner,cursor=cursor,limit=1)
        finally:
            await earlier.rollback();await later.rollback()
