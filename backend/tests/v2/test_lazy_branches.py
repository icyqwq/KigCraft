import asyncio
from datetime import timedelta
import pytest
from sqlalchemy import func, select
from app.contracts.v2 import ContinuationDraft
from app.core.time import utc_now
from app.design.manual import SaveManualRevision, save_manual
from app.design.models import Branch, CodexEpoch, Design, Operation, Revision, Wallet
from app.design.service import ApplicationError
from app.operations.leases import recover_expired
from .test_operations import factory, quoted, request, submit_request
from .test_migrations import migration_database
from .test_worker import prepared, ScriptedRpc
from .test_history_boundaries import next_turn, revision_for

async def retry_request(factory, owner, branch, revision):
    async with factory() as db:
        current = await db.get(Branch,branch)
        return request(text="",regenerate_revision_id=revision.id,client_request_id="retry-request",
                       expected_branch_version=current.state_version)

async def test_quote_preview_creates_no_branch_and_submission_is_atomic_idempotent(factory,tmp_path):
    worker,runtime,owner,branch,opid=await prepared(factory,tmp_path)
    await worker.run_once()
    image=await revision_for(factory,opid)
    payload=await quoted(factory,owner,branch,await retry_request(factory,owner,branch,image))
    async with factory() as db:
        assert (await db.execute(select(func.count()).select_from(Branch))).scalar_one()==1
        assert (await db.get(Wallet,owner)).reserved==0
    a,b=await asyncio.gather(submit_request(factory,owner,branch,payload),submit_request(factory,owner,branch,payload))
    assert a==b
    async with factory() as db:
        operation=await db.get(Operation,a)
        assert operation.branch_id!=branch and operation.source_operation_id==opid
        assert operation.input_snapshot_id==(await db.get(Operation,opid)).input_snapshot_id
        assert (await db.get(Wallet,owner)).reserved==1
        assert (await db.execute(select(func.count()).select_from(Branch))).scalar_one()==2

async def test_retry_second_round_forks_before_it_and_never_uses_future_base(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path)
    await worker.run_once()
    first_image=await revision_for(factory,first)
    second,image=await next_turn(factory,worker,owner,branch,"Slight smile","second-request")
    third,future=await next_turn(factory,worker,owner,branch,"Future violet wig","third-request")
    payload=await quoted(factory,owner,branch,await retry_request(factory,owner,branch,image))
    retry=await submit_request(factory,owner,branch,payload)
    assert await worker.run_once()==retry
    forks=[c for c in runtime.calls if c[0] and c[1]=="thread/fork"]
    async with factory() as db:
        original=await db.get(Operation,first)
        assert len(forks)==1 and forks[0][2]["lastTurnId"]==original.upstream_turn_id
        op=await db.get(Operation,retry)
        assert op.state=="succeeded" and op.base_revision_id==first_image.id
        assert op.context_before==(await db.get(Operation,second)).context_before
        assert (await db.get(Branch,branch)).current_revision_id==future.id
        assert (await db.get(Branch,op.branch_id)).current_revision_id!=future.id
        assert op.context_allocation["state"]=="bound"
    main_calls=[c for c in runtime.calls if c[0] and c[1]=="turn/start"]
    assert main_calls[-1][2]["input"][0]["text"]=="Slight smile"

async def test_retry_first_creates_new_thread_without_fork(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path)
    await worker.run_once()
    image=await revision_for(factory,first)
    payload=await quoted(factory,owner,branch,await retry_request(factory,owner,branch,image))
    retry=await submit_request(factory,owner,branch,payload)
    await worker.run_once()
    assert not any(c[1]=="thread/fork" for c in runtime.calls)
    assert len([c for c in runtime.calls if c[0] and c[1]=="thread/start"])==2
    async with factory() as db:
        assert (await db.get(Operation,retry)).state=="succeeded"

async def test_old_revision_continuation_forks_at_selected_image(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path)
    await worker.run_once()
    image=await revision_for(factory,first)
    await next_turn(factory,worker,owner,branch,"Future change","second-request")
    payload=request(text="Use this old image",client_request_id="continue-old",base_revision_id=image.id,
        expected_branch_version=2,continuation=ContinuationDraft(source_revision_id=image.id))
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch,payload))
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="succeeded" and op.branch_id!=branch
        assert op.base_revision_id==image.id
        first_op=await db.get(Operation,first)
        forks=[c for c in runtime.calls if c[1]=="thread/fork"]
        assert forks[0][2]["lastTurnId"]==first_op.upstream_turn_id

async def test_save_old_image_forks_without_model_then_next_turn_allocates_exact_context(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path)
    await worker.run_once()
    image=await revision_for(factory,first)
    await next_turn(factory,worker,owner,branch,"Future change","second-request")
    count=len(runtime.calls)
    payload=SaveManualRevision(client_request_id="manual-old",base_revision_id=image.id,
        expected_branch_version=2,clean_asset_id=image.clean_asset_id,recipe={"mouthSmile":0.3},
        continuation=ContinuationDraft(source_revision_id=image.id))
    async with factory.begin() as db:
        saved=await save_manual(db,owner,branch,payload)
    async with factory.begin() as db:
        assert (await save_manual(db,owner,branch,payload)).id==saved.id
        assert saved.branch_id!=branch and saved.context_checkpoint==image.context_checkpoint
    assert len(runtime.calls)==count
    opid,_=await next_turn(factory,worker,owner,saved.branch_id,"Keep the smile","manual-next")
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="succeeded"
        assert op.context_before==image.context_checkpoint
    assert [c for c in runtime.calls if c[1]=="thread/fork"][-1][2]["lastTurnId"]==image.context_checkpoint["turn_id"]

async def test_insufficient_wallet_rolls_back_branch_creation(factory,tmp_path):
    worker,runtime,owner,branch,opid=await prepared(factory,tmp_path)
    await worker.run_once()
    image=await revision_for(factory,opid)
    payload=await quoted(factory,owner,branch,await retry_request(factory,owner,branch,image))
    async with factory.begin() as db:
        (await db.get(Wallet,owner)).balance=0
    with pytest.raises(ApplicationError,match="quota_insufficient"):
        await submit_request(factory,owner,branch,payload)
    async with factory() as db:
        assert (await db.execute(select(func.count()).select_from(Branch))).scalar_one()==1

async def test_lost_fork_ack_is_durable_and_never_replayed(factory,tmp_path):
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path)
    await worker.run_once()
    _,image=await next_turn(factory,worker,owner,branch,"Slight smile","second-request")
    payload=await quoted(factory,owner,branch,await retry_request(factory,owner,branch,image))
    opid=await submit_request(factory,owner,branch,payload)
    class LostFork(ScriptedRpc):
        async def request(self,method,params):
            if method=="thread/fork":
                # A second DB transaction can lock the operation: no transaction spans the RPC.
                async with worker.factory.begin() as db:
                    op=(await db.execute(select(Operation).where(Operation.id==opid).with_for_update(nowait=True))).scalar_one()
                    assert op.context_allocation["state"]=="sent"
                self.runtime.calls.append((self.images,method,params))
                raise OSError("response lost")
            return await super().request(method,params)
    runtime.connection=lambda workspace,images:LostFork(runtime,workspace,images)
    before=len([c for c in runtime.calls if c[0] and c[1]=="turn/start"])
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="failed" and op.error_code=="context_allocation_unknown"
        assert op.context_allocation["state"]=="unknown" and op.dispatch_intent_at is None
        assert (await db.get(Wallet,owner)).reserved==0
    assert await worker.run_once() is None
    assert len([c for c in runtime.calls if c[0] and c[1]=="turn/start"])==before
    assert len([c for c in runtime.calls if c[1]=="thread/fork"])==1

async def test_crash_after_allocation_intent_does_not_allocate_again(factory,tmp_path):
    worker,runtime,owner,branch,opid=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        op=await db.get(Operation,opid)
        op.state="reviewing";op.context_allocation={"state":"sent"}
        op.lease_owner="dead-worker";op.lease_expires_at=utc_now()-timedelta(seconds=1)
    await worker.recover_expired()
    await worker.run_once()
    assert runtime.calls==[]
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="failed" and op.error_code=="context_allocation_unknown"
        assert (await db.get(Wallet,owner)).reserved==0


async def test_bound_context_survives_failure_before_dispatch_without_second_fork(factory,tmp_path,monkeypatch):
    import app.operations.worker as module
    from app.operations.regenerate import regeneration_plan
    worker,runtime,owner,branch,first=await prepared(factory,tmp_path)
    await worker.run_once()
    _,image=await next_turn(factory,worker,owner,branch,"Slight smile","second-request")
    payload=await quoted(factory,owner,branch,await retry_request(factory,owner,branch,image))
    retry=await submit_request(factory,owner,branch,payload)
    original_record=module.record_dispatch_intent
    async def failed_record(*args,**kwargs):raise OSError("database unavailable before dispatch")
    monkeypatch.setattr(module,"record_dispatch_intent",failed_record)
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,retry)
        assert op.state=="failed" and op.context_allocation["state"]=="bound"
        assert op.dispatch_intent_at is None
        child=op.branch_id;checkpoint=op.context_before
    monkeypatch.setattr(module,"record_dispatch_intent",original_record)
    next_id,result=await next_turn(factory,worker,owner,child,"Continue from the allocated context","bound-next")
    async with factory() as db:
        assert (await db.get(Operation,next_id)).context_before==checkpoint
        plan=await regeneration_plan(db,owner,result.id)
        assert plan.boundary.turn_id==checkpoint["turn_id"]
    assert len([c for c in runtime.calls if c[1]=="thread/fork"])==1
