import asyncio
from datetime import timedelta
import pytest
from sqlalchemy import select,func
from app.core.time import utc_now
from app.contracts.v2 import AnswerBinding,InputSnapshot
from app.conversation.questions import saved,question_state
from app.design.models import Branch,CodexEpoch,InputRecord,Message,Operation,Wallet,DomainEvent
from app.design.service import ApplicationError
from .test_operations import factory,request,quoted,submit_request
from .test_migrations import migration_database
from .test_worker import prepared
from .test_api import api

async def question(factory,tmp_path):
    worker,runtime,owner,branch,opid=await prepared(factory,tmp_path,"clarification")
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="awaiting_input" and op.reservation_closed and op.question
        assert await question_state(db,op)=="open"
        assert (await db.get(Wallet,owner)).balance==5
        value=saved(op)
    return worker,runtime,owner,branch,opid,value

def answering(opid,value,**extra):
    return request(client_request_id="answer-request",text="Coral, please",
        answer_to=AnswerBinding(operation_id=opid,version=value.version),
        base_revision_id=value.base_revision_id,expected_branch_version=value.branch_version,**extra)

async def test_answer_reuses_exact_question_context_and_idempotent_acceptance(factory,tmp_path):
    worker,runtime,owner,branch,opid,value=await question(factory,tmp_path)
    payload=await quoted(factory,owner,branch,answering(opid,value))
    first,second=await asyncio.gather(submit_request(factory,owner,branch,payload),submit_request(factory,owner,branch,payload))
    assert first==second
    async with factory() as db:
        source=await db.get(Operation,opid);answer=await db.get(Operation,first)
        assert await question_state(db,source)=="answered" and source.question["answered_by"]==first
        assert answer.context_before==value.checkpoint and answer.branch_id==branch
        assert (await db.get(Wallet,owner)).reserved==1
        assert (await db.get(InputRecord,answer.input_snapshot_id)).snapshot["user_text"]=="Coral, please"
        assert await db.scalar(select(func.count()).select_from(DomainEvent).where(DomainEvent.event_type=="question.resolved"))==1
    runtime.mode="image"
    await worker.run_once()
    assert any(c[1]=="thread/resume" and c[2]["threadId"]=="main-thread" for c in runtime.calls)
    async with factory() as db:
        assert (await db.get(Operation,first)).state=="succeeded"
        assert (await db.get(Wallet,owner)).balance==4
        assert await db.scalar(select(func.count()).select_from(Branch))==1

@pytest.mark.parametrize("change",["time","branch","epoch","later","version"])
async def test_expired_question_or_wrong_version_rejects_before_reservation(factory,tmp_path,change):
    worker,runtime,owner,branch,opid,value=await question(factory,tmp_path)
    payload=await quoted(factory,owner,branch,answering(opid,value))
    async with factory.begin() as db:
        op=await db.get(Operation,opid)
        if change=="time":op.question={**op.question,"expires_at":(utc_now()-timedelta(seconds=1)).isoformat()}
        if change=="branch":(await db.get(Branch,branch)).state_version+=1
        if change=="epoch":(await db.get(CodexEpoch,(await db.get(Branch,branch)).active_epoch_id)).checkpoint_turn_id="other"
        if change=="later":
            db.add(Message(branch_id=branch,operation_id=opid,role="user",content_parts=[{"type":"text","text":"later"}]))
    if change=="version":
        payload=payload.model_copy(update={"answer_to":AnswerBinding(operation_id=opid,version=2)})
    with pytest.raises(ApplicationError,match="question_version_conflict" if change=="version" else "question_expired"):
        await submit_request(factory,owner,branch,payload)
    async with factory() as db:
        assert (await db.get(Wallet,owner)).reserved==0
        assert await db.scalar(select(func.count()).select_from(Operation))==1

async def test_worker_refuses_changed_question_boundary_before_any_model_call(factory,tmp_path):
    worker,runtime,owner,branch,opid,value=await question(factory,tmp_path)
    payload=await quoted(factory,owner,branch,answering(opid,value))
    answer=await submit_request(factory,owner,branch,payload)
    async with factory.begin() as db:
        (await db.get(CodexEpoch,(await db.get(Branch,branch)).active_epoch_id)).checkpoint_turn_id="changed"
    before=len(runtime.calls)
    await worker.run_once()
    assert len(runtime.calls)==before
    async with factory() as db:
        assert (await db.get(Operation,answer)).error_code=="question_expired"
        assert (await db.get(Wallet,owner)).reserved==0

async def test_http_private_question_expiry_and_answer_endpoint(api,tmp_path):
    from app.core.config import get_settings
    from app.operations.worker import LocalWorker
    from .test_worker import ScriptedRuntime
    client,factory,owner,design,branch=api
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    worker=LocalWorker(factory,get_settings().model_copy(update={"v2_runtime_dir":str(tmp_path/"runtime")}),runtime=ScriptedRuntime("clarification"))
    await worker.run_once()
    async with factory() as db:
        value=saved(await db.get(Operation,opid))
    response=await client.get(f"/api/v2/operations/{opid}/question")
    assert response.status_code==200,response.text
    data=response.json()
    assert response.headers["cache-control"]=="private, no-store"
    assert data["state"]=="open" and data["messages"][0]["text"]=="Which color?"
    assert "checkpoint" not in data and "main-thread" not in response.text
    payload=answering(opid,value).model_dump(mode="json")
    payload.pop("quote_id")
    price=await client.post(f"/api/v2/branches/{branch}/quotes",json=payload)
    assert price.status_code==200,price.text
    payload["quote_id"]=price.json()["id"]
    accepted=await client.post(f"/api/v2/operations/{opid}/answers",json=payload)
    assert accepted.status_code==202,accepted.text
    replay=await client.post(f"/api/v2/operations/{opid}/answers",json=payload)
    assert replay.json()["id"]==accepted.json()["id"]
    assert (await client.get(f"/api/v2/operations/{opid}/question")).json()["state"]=="answered"
    _,_,_,_,foreign=await prepared(factory,tmp_path/"foreign","clarification")
    async with factory.begin() as db:
        (await db.get(Operation,foreign)).question=(await db.get(Operation,opid)).question
    assert (await client.get(f"/api/v2/operations/{foreign}/question")).status_code==404
    payload["answer_to"]["operation_id"]=foreign
    assert (await client.post(f"/api/v2/operations/{foreign}/answers",json=payload)).status_code==404
