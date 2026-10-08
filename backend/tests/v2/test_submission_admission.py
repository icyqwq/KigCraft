import asyncio
from datetime import timedelta
import pytest
from sqlalchemy import func, select, update
from app.core.config import get_settings
from app.core.time import utc_now
from app.design.models import SubmissionAdmission, Operation, Wallet, Message
from app.design.service import ApplicationError, create_design
from app.operations.submission_admission import SubmissionRateLimited
from .test_operations import factory,setup,quoted,request,submit_request
from .test_migrations import migration_database
from .test_api import api

@pytest.fixture
def rate(monkeypatch):
    monkeypatch.setenv("V2_SUBMIT_RATE_LIMIT_WINDOW_SECONDS","60")
    monkeypatch.setenv("V2_SUBMIT_RATE_LIMIT_MAX_REQUESTS","1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

async def branch_for(factory,owner,key):
    async with factory.begin() as db:
        return (await create_design(db,owner,key,key)).last_branch_id

async def admissions(factory):
    async with factory() as db:
        return await db.scalar(select(func.count()).select_from(SubmissionAdmission))

async def test_concurrent_designs_share_account_limit_and_replay_costs_no_slot(factory,rate):
    owner,_,first=await setup(factory)
    second=await branch_for(factory,owner,"second-design")
    payloads=[await quoted(factory,owner,b,request(client_request_id="request-"+str(i))) for i,b in enumerate([first,second])]
    results=await asyncio.gather(*(submit_request(factory,owner,b,p) for b,p in zip([first,second],payloads)),return_exceptions=True)
    assert sum(isinstance(item,SubmissionRateLimited) for item in results)==1
    winner=next(i for i,item in enumerate(results) if isinstance(item,str))
    assert await submit_request(factory,owner,[first,second][winner],payloads[winner])==results[winner]
    assert await admissions(factory)==1
    async with factory() as db:
        assert (await db.get(Wallet,owner)).reserved==1
        assert await db.scalar(select(func.count()).select_from(Operation))==1
        assert await db.scalar(select(func.count()).select_from(Message))==1

async def test_failed_wallet_transaction_does_not_consume_frequency(factory,rate):
    owner,_,branch=await setup(factory,balance=0)
    payload=await quoted(factory,owner,branch)
    with pytest.raises(ApplicationError,match="quota_insufficient"):
        await submit_request(factory,owner,branch,payload)
    assert await admissions(factory)==0
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Operation))==0

async def test_invalid_quote_does_not_consume_frequency(factory,rate):
    owner,_,branch=await setup(factory)
    with pytest.raises(ApplicationError,match="quote_invalid"):
        await submit_request(factory,owner,branch,request())
    assert await admissions(factory)==0

async def test_separate_accounts_have_separate_limits(factory,rate):
    for _ in range(2):
        owner,_,branch=await setup(factory)
        await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    assert await admissions(factory)==2

async def test_expired_window_accepts_original_rejected_request(factory,rate):
    owner,_,first=await setup(factory)
    second=await branch_for(factory,owner,"second-design")
    await submit_request(factory,owner,first,await quoted(factory,owner,first))
    payload=await quoted(factory,owner,second,request(client_request_id="second-request"))
    with pytest.raises(SubmissionRateLimited) as rejected:
        await submit_request(factory,owner,second,payload)
    assert 1<=rejected.value.retry_after<=60
    async with factory.begin() as db:
        await db.execute(update(SubmissionAdmission).where(SubmissionAdmission.owner_id==owner)
            .values(created_at=utc_now()-timedelta(seconds=61)))
    await submit_request(factory,owner,second,payload)
    assert await admissions(factory)==2

async def test_question_answer_limit_keeps_question_and_wallet_unchanged(factory,tmp_path,rate):
    from .test_questions import question,answering
    from app.conversation.questions import question_state
    _,_,owner,branch,opid,value=await question(factory,tmp_path)
    payload=await quoted(factory,owner,branch,answering(opid,value))
    with pytest.raises(SubmissionRateLimited):
        await submit_request(factory,owner,branch,payload)
    async with factory() as db:
        source=await db.get(Operation,opid)
        assert await question_state(db,source)=="open"
        assert source.question.get("answered_by") is None
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,0)
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.role=="user"))==1
    assert await admissions(factory)==1

async def test_http_rate_response_is_distinct_from_quota_and_preserves_inputs(api,rate):
    client,factory,owner,_,first=api
    second=await branch_for(factory,owner,"second-design")
    original=await quoted(factory,owner,first)
    initial=await client.post("/api/v2/branches/"+first+"/turns",json=original.model_dump(mode="json"))
    assert initial.status_code==202
    payload=await quoted(factory,owner,second,request(client_request_id="second-request"))
    limited=await client.post("/api/v2/branches/"+second+"/turns",json=payload.model_dump(mode="json"))
    assert limited.status_code==429
    assert limited.json()["code"]=="submission_rate_limited"
    assert limited.json()["quota_outcome"]=="unchanged"
    assert limited.json()["retry_action"]=="wait"
    assert 1<=int(limited.headers["Retry-After"])<=60
    assert limited.json()["retry_after_seconds"]==int(limited.headers["Retry-After"])
    replay=await client.post("/api/v2/branches/"+first+"/turns",json=original.model_dump(mode="json"))
    assert replay.status_code==202 and replay.json()["id"]==initial.json()["id"]
    assert await admissions(factory)==1
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,1)


