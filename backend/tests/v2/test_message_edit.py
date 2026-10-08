import asyncio
from uuid import uuid4
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from app.contracts.v2 import ContinuationDraft, InputSnapshot
from app.design.models import Asset, Branch, Design, InputRecord, Message, Operation, Wallet
from app.design.service import ApplicationError
from app.operations.message_edit import prepare_message_edit
from .test_operations import factory, setup, request, quoted, submit_request
from .test_migrations import migration_database
from .test_worker import prepared
from .test_history_boundaries import next_turn, revision_for
from .test_api import api


async def user_message(factory,opid):
    async with factory() as db:
        return await db.scalar(select(Message).where(Message.operation_id==opid,Message.role=="user"))


def editing(message,base,version,**extra):
    return request(client_request_id="edit-"+str(uuid4()),text="Edited request",base_revision_id=base,
        expected_branch_version=version,continuation=ContinuationDraft(source_revision_id=base,boundary_message_id=message.id),**extra)


async def test_second_message_edit_forks_before_original_and_keeps_original_history(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path);await worker.run_once()
    first_image=await revision_for(factory,first)
    second,_=await next_turn(factory,worker,owner,branch,"Smile","second-request")
    third,future=await next_turn(factory,worker,owner,branch,"Future violet wig","third-request")
    message=await user_message(factory,second)
    async with factory() as db:
        original=await prepare_message_edit(db,owner,branch,message.id)
        digest=original.inputs.digest();before_calls=len(runtime.calls)
        assert original.inputs.user_text=="Smile"
        assert await db.scalar(select(func.count()).select_from(Branch))==1
    payload=await quoted(factory,owner,branch,editing(message,first_image.id,3))
    assert len(runtime.calls)==before_calls
    a,b=await asyncio.gather(submit_request(factory,owner,branch,payload),submit_request(factory,owner,branch,payload))
    assert a==b
    await worker.run_once()
    async with factory() as db:
        edit=await db.get(Operation,a);child=await db.get(Branch,edit.branch_id)
        assert child.parent_branch_id==branch and child.fork_boundary_message_id==message.id
        assert child.history_parent_sequence==message.history_sequence-1
        assert edit.context_before==(await db.get(Operation,second)).context_before
        assert edit.base_revision_id==first_image.id and edit.source_operation_id==second
        assert (await db.get(Branch,branch)).current_revision_id==future.id
        source=await prepare_message_edit(db,owner,branch,message.id)
        assert source.inputs.digest()==digest
        snapshot=InputSnapshot.model_validate((await db.get(InputRecord,edit.input_snapshot_id)).snapshot)
        assert snapshot.user_text=="Edited request" and snapshot.base_revision_id==first_image.id
        assert edit.input_snapshot_id!=source.operation.input_snapshot_id
        assert (await db.get(Wallet,owner)).balance==1
        first_op=await db.get(Operation,first)
        assert [c for c in runtime.calls if c[1]=="thread/fork"][-1][2]["lastTurnId"]==first_op.upstream_turn_id
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:
            await db.execute(text("UPDATE v2_branches SET fork_boundary_message_id=NULL WHERE id=:id"),{"id":child.id})


async def test_edit_first_message_starts_fresh_and_rejects_future_message_in_child(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path);await worker.run_once()
    second,_=await next_turn(factory,worker,owner,branch,"Future","second-request")
    first_message=await user_message(factory,first)
    payload=await quoted(factory,owner,branch,editing(first_message,None,2))
    edit=await submit_request(factory,owner,branch,payload);await worker.run_once()
    async with factory() as db:
        operation=await db.get(Operation,edit);child=await db.get(Branch,operation.branch_id)
        assert child.origin_revision_id is None and child.context_seed=={"kind":"fresh"}
        assert child.history_parent_sequence==0
        future_message=await user_message(factory,second)
        with pytest.raises(ApplicationError,match="not_found"):
            await prepare_message_edit(db,owner,child.id,future_message.id)
    assert not any(c[1]=="thread/fork" for c in runtime.calls)
    assert len([c for c in runtime.calls if c[0] and c[1]=="thread/start"])==2


async def test_edit_references_are_new_snapshot_and_old_quote_cannot_authorize_changes(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path);await worker.run_once()
    message=await user_message(factory,first)
    async with factory.begin() as db:
        asset=Asset(owner_id=owner,object_key="new-ref",sha256="b"*64,mime_type="image/png",kind="reference",width=16,height=16,byte_size=10)
        db.add(asset);await db.flush()
    payload=await quoted(factory,owner,branch,editing(message,None,1,attachments=[{"asset_id":asset.id,"usage":"new usage"}]))
    with pytest.raises(ApplicationError,match="quote_invalid"):
        await submit_request(factory,owner,branch,payload.model_copy(update={"text":"different"}))
    changed=await submit_request(factory,owner,branch,payload)
    async with factory() as db:
        op=await db.get(Operation,changed);snap=InputSnapshot.model_validate((await db.get(InputRecord,op.input_snapshot_id)).snapshot)
        assert [(r.asset_id,r.usage,r.ordinal) for r in snap.references]==[(asset.id,"new usage",1)]
        assert (await prepare_message_edit(db,owner,branch,message.id)).inputs.references==()
        assert (await db.get(Wallet,owner)).reserved==1


async def test_local_message_edit_retains_original_mask_recipe_and_annotation_ids(factory,tmp_path,monkeypatch):
    from app.core.config import get_settings
    from .test_local_edit import accepted
    monkeypatch.setenv("V2_LOCAL_EDIT_ENABLED","true");monkeypatch.setenv("V2_STORAGE_DIR",str(tmp_path/"assets"));get_settings.cache_clear()
    try:
        worker,runtime,owner,branch,base,opid,_=await accepted(factory,tmp_path,monkeypatch,mode="preserve")
        await worker.run_once();message=await user_message(factory,opid)
        payload=await quoted(factory,owner,branch,editing(message,base.id,2))
        edited=await submit_request(factory,owner,branch,payload)
        async with factory() as db:
            original=await prepare_message_edit(db,owner,branch,message.id)
            op=await db.get(Operation,edited);inputs=InputSnapshot.model_validate((await db.get(InputRecord,op.input_snapshot_id)).snapshot)
            assert op.kind=="local_edit" and inputs.local_edit_mode=="preserve"
            assert inputs.prepared_edit==original.inputs.prepared_edit
            assert inputs.references==original.inputs.references
            assert inputs.annotation_review()==original.inputs.annotation_review()
    finally:get_settings.cache_clear()


async def test_http_preview_is_private_read_only_and_rejects_cross_owner(api,tmp_path):
    from app.operations.worker import LocalWorker
    from app.core.config import get_settings
    from .test_worker import ScriptedRuntime
    client,factory,owner,design,branch=api
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    worker=LocalWorker(factory,get_settings().model_copy(update={"v2_runtime_dir":str(tmp_path/"runtime")}),runtime=ScriptedRuntime())
    await worker.run_once();message=await user_message(factory,opid)
    path=f"/api/v2/branches/{branch}/messages/{message.id}/edit"
    before=(await client.get(f"/api/v2/designs/{design}")).json();calls=len(worker.runtime.calls)
    response=await client.get(path);assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="private, no-store"
    assert response.json()["text"]=="coral wig" and response.json()["source_revision_id"] is None
    assert "thread" not in repr(response.json()) and "epoch" not in repr(response.json())
    assert (await client.get(f"/api/v2/designs/{design}")).json()==before and len(worker.runtime.calls)==calls
    _,_,foreign=await setup(factory)
    assert (await client.get(f"/api/v2/branches/{foreign}/messages/{message.id}/edit")).status_code==404
    async with factory() as db:
        assistant=await db.scalar(select(Message).where(Message.operation_id==opid,Message.role=="assistant"))
    assert (await client.get(f"/api/v2/branches/{branch}/messages/{assistant.id}/edit")).status_code==404


async def test_stale_branch_wrong_base_and_unconfirmed_operation_refuse_without_branch(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path)
    message=await user_message(factory,first)
    async with factory() as db:
        with pytest.raises(ApplicationError,match="history_checkpoint_unconfirmed"):
            await prepare_message_edit(db,owner,branch,message.id)
    await worker.run_once();image=await revision_for(factory,first)
    for base,version,code in ((None,0,"state_conflict"),(image.id,1,"state_conflict")):
        with pytest.raises(ApplicationError,match=code):
            await quoted(factory,owner,branch,editing(message,base,version))
    async with factory() as db:assert await db.scalar(select(func.count()).select_from(Branch))==1
