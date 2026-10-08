import asyncio
import json
import pytest
from sqlalchemy import select,func
from app.design.models import Operation,Wallet,Message
from app.design.service import ApplicationError
from app.conversation.questions import saved,question_state
from .test_operations import factory,quoted,submit_request
from .test_migrations import migration_database
from .test_questions import question,answering
from .test_reconciliation import unknown

async def test_distinct_concurrent_answers_cannot_consume_question_twice(factory,tmp_path):
    worker,runtime,owner,branch,opid,value=await question(factory,tmp_path)
    first=await quoted(factory,owner,branch,answering(opid,value))
    second=await quoted(factory,owner,branch,answering(opid,value).model_copy(update={"client_request_id":"second-answer-request"}))
    results=await asyncio.gather(submit_request(factory,owner,branch,first),
                                 submit_request(factory,owner,branch,second),return_exceptions=True)
    assert sum(isinstance(result,str) for result in results)==1
    failure=next(result for result in results if isinstance(result,Exception))
    assert isinstance(failure,ApplicationError) and failure.code=="question_expired"
    async with factory() as db:
        assert (await db.get(Wallet,owner)).reserved==1
        assert await db.scalar(select(func.count()).select_from(Operation))==2

async def test_another_clarification_opens_distinct_question_and_releases_all_reservations(factory,tmp_path):
    worker,runtime,owner,branch,opid,value=await question(factory,tmp_path)
    answer=await submit_request(factory,owner,branch,await quoted(factory,owner,branch,answering(opid,value)))
    await worker.run_once()
    async with factory() as db:
        first=await db.get(Operation,opid);second=await db.get(Operation,answer)
        assert await question_state(db,first)=="answered"
        assert await question_state(db,second)=="open"
        assert first.question["messages"]==value.messages
        assert saved(second).checkpoint!=value.checkpoint
        assert (await db.get(Wallet,owner)).balance==5 and (await db.get(Wallet,owner)).reserved==0

async def test_readonly_recovery_can_open_completed_question_without_dispatch(factory,tmp_path):
    worker,runtime,owner,branch,opid,response,calls=await unknown(factory,tmp_path)
    turn=response["thread"]["turns"][0]
    turn["items"]=[turn["items"][0],{"id":"question","type":"agentMessage","text":json.dumps({"schema_version":1,"kind":"question","text":"Which wig color?"}),"phase":"final_answer"}]
    assert await worker.reconcile(opid)=="awaiting_input"
    assert calls==["thread/read"]
    async with factory() as db:
        operation=await db.get(Operation,opid)
        assert await question_state(db,operation)=="open"
        assert operation.question["messages"][0]["text"]=="Which wig color?"
        assert (await db.get(Wallet,owner)).reserved==0
