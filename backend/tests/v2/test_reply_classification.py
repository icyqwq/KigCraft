"""Reply intent changes question affordances, never delivery or settlement."""
import json
import pytest
from sqlalchemy import select, func
from app.conversation.replies import message_parts, message_prefix, REPLY_SCHEMA
from app.design.models import Message, Operation, Revision, Wallet, DomainEvent
from app.design.router import message_json
from app.design.service import ApplicationError
from app.operations.lifecycle import message_completed
from .test_operations import factory
from .test_migrations import migration_database
from .test_worker import prepared, ScriptedRpc
from .test_reconciliation import unknown

def envelope(kind="response", text="The coral wig is confirmed."):
    return json.dumps({"schema_version":1,"kind":kind,"text":text})

@pytest.mark.parametrize("body", [
    '{"schema_version":1,"kind":"question","text":"Color?","unexpected":"private"}',
    '{"schema_version":1,"kind":"question","kind":"response","text":"Color?"}',
    '{"schema_version":true,"kind":"question","text":"Color?"}',
    '{"schema_version":3,"kind":"question","text":"Color?"}',
    '{"schema_version":1,"kind":"question","text":""}',
    '{"schema_version":1,"kind":"question","text":"![image](C:/private/output.png)"}',
    '{"schema_version":1,"kind":"question","text":"Unfinished',
    '```json\n{"kind":"question"}\n```',
])
def test_invalid_envelopes_do_not_publish_transport_or_create_questions(body):
    assert message_prefix(body)==""
    assert message_parts({"text":body,"phase":"final_answer"})==[
        {"type":"reply_intent","schema_version":1,"kind":"invalid"}]

def test_streaming_holds_envelope_but_preserves_commentary_and_plain_legacy_replies():
    for size in range(1,len(envelope())):
        assert message_prefix(envelope()[:size])==""
    assert message_prefix("I will keep the coral wig. ")=="I will keep the coral"
    assert message_parts({"text":"The label says 'Which color?'."})==[
        {"type":"text","text":"The label says 'Which color?'."}]
    assert message_parts({"text":envelope("question","Color?"),"phase":"commentary"})[0]["kind"]=="invalid"

@pytest.mark.parametrize("body,expected,has_question", [
    (envelope("question","Which wig color?"),"awaiting_input",True),
    (envelope(),"succeeded",False),
    (envelope("response","The label 'Which color?' was removed."),"succeeded",False),
    ("Confirmed. No image requested.","succeeded",False),
    ("Which color?","succeeded",False),  # Unknown historical plain text is never reclassified.
    ('{"schema_version":1,"kind":"question","text":false}',"failed",False),
])
async def test_no_image_final_classification_and_zero_charge(factory,tmp_path,monkeypatch,body,expected,has_question):
    original=ScriptedRpc.request
    async def request(self,method,params):
        response=await original(self,method,params)
        if self.images and method=="turn/start":
            assert params["outputSchema"]==REPLY_SCHEMA
            self.events=[self.item({"id":"message","type":"agentMessage","phase":"final_answer","text":body}),self.complete("completed")]
        return response
    monkeypatch.setattr(ScriptedRpc,"request",request)
    worker,runtime,owner,branch,opid=await prepared(factory,tmp_path,"clarification")
    await worker.run_once()
    async with factory() as db:
        operation=await db.get(Operation,opid)
        assert operation.state==expected and bool(operation.question)==has_question
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,0)
        assert await db.scalar(select(func.count()).select_from(Revision))==0
        assert await db.scalar(select(func.count()).select_from(DomainEvent).where(DomainEvent.event_type=="question.opened"))==int(has_question)
        messages=(await db.scalars(select(Message).where(Message.operation_id==opid,Message.role=="assistant"))).all()
        public=[message_json(m) for m in messages]
        assert all("reply_intent" not in json.dumps(m,default=str) and "schema_version" not in json.dumps(m,default=str) for m in public)
        if expected=="failed":
            assert operation.error_code=="reply_invalid" and public==[None]
    assert await worker.run_once() is None

async def test_final_response_overrides_earlier_question(factory,tmp_path,monkeypatch):
    original=ScriptedRpc.request
    async def request(self,method,params):
        response=await original(self,method,params)
        if self.images and method=="turn/start":
            self.events=[
                self.item({"id":"early","type":"agentMessage","phase":"final_answer","text":envelope("question","Which color?")}),
                self.item({"id":"final","type":"agentMessage","phase":"final_answer","text":envelope()}),
                self.complete("completed")]
        return response
    monkeypatch.setattr(ScriptedRpc,"request",request)
    worker,runtime,owner,branch,opid=await prepared(factory,tmp_path,"clarification")
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="succeeded" and op.question is None

@pytest.mark.parametrize("kind,expected", [("question","awaiting_input"),("response","succeeded")])
async def test_readonly_recovery_preserves_same_classification(factory,tmp_path,kind,expected):
    worker,runtime,owner,branch,opid,response,calls=await unknown(factory,tmp_path)
    turn=response["thread"]["turns"][0]
    turn["items"]=[turn["items"][0],{"id":"final","type":"agentMessage","phase":"final_answer",
        "text":envelope(kind,"Which wig color?" if kind=="question" else "Confirmed.")}]
    assert await worker.reconcile(opid)==expected
    assert calls==["thread/read"]
    async with factory() as db:
        assert (await db.get(Wallet,owner)).reserved==0
        assert bool((await db.get(Operation,opid)).question)==(kind=="question")

async def test_duplicate_final_cannot_switch_reply_intent(factory):
    from .test_streaming import prepare
    owner,design,branch,opid,token=await prepare(factory)
    async with factory.begin() as db:
        await message_completed(db,opid,"worker",token,"turn",{"id":"final","text":envelope("question","Color?")})
    async with factory.begin() as db:
        with pytest.raises(ApplicationError,match="message_identity_conflict"):
            await message_completed(db,opid,"worker",token,"turn",{"id":"final","text":envelope("response","Color?")})

@pytest.mark.parametrize("final_kind,expected", [(None,"failed"),("question","awaiting_input"),("response","succeeded")])
async def test_commentary_cannot_stand_in_for_or_override_final_reply(factory,tmp_path,monkeypatch,final_kind,expected):
    original=ScriptedRpc.request
    async def request(self,method,params):
        response=await original(self,method,params)
        if self.images and method=="turn/start":
            self.events=[]
            if final_kind:
                self.events.append(self.item({"id":"final","type":"agentMessage","phase":"final_answer",
                    "text":envelope(final_kind,"Which color?" if final_kind=="question" else "Confirmed.")}))
            self.events.extend([self.item({"id":"commentary","type":"agentMessage","phase":"commentary","text":"I am checking the wig."}),self.complete("completed")])
        return response
    monkeypatch.setattr(ScriptedRpc,"request",request)
    worker,runtime,owner,branch,opid=await prepared(factory,tmp_path,"clarification")
    await worker.run_once()
    async with factory() as db:
        operation=await db.get(Operation,opid)
        assert operation.state==expected and bool(operation.question)==(final_kind=="question")
        if operation.question:assert len(operation.question["messages"])==1 and operation.question["messages"][0]["text"]=="Which color?"
        assert (await db.get(Wallet,owner)).reserved==0
