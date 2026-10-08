import copy
import pytest
from sqlalchemy import select,func
from app.auth.models import UserRole
from app.db.models import User
from app.design.models import RuntimeObservation,Wallet,Operation
from app.design.service import ApplicationError
from app.operations.observations import observer,summary
from app.operations.leases import claim
from .test_worker import prepared,ScriptedRpc
from .test_operations import factory
from .test_migrations import migration_database
from .test_api import api
from .test_runtime_usage import usage

async def rows(factory,opid):
    async with factory() as db:
        return list((await db.scalars(select(RuntimeObservation).where(
            RuntimeObservation.operation_id==opid).order_by(RuntimeObservation.created_at))).all())

async def test_worker_records_review_generation_without_fabricating_tokens(factory,tmp_path):
    worker,runtime,owner,_,opid=await prepared(factory,tmp_path)
    assert await worker.run_once()==opid
    first=await rows(factory,opid)
    assert [r.phase for r in first]==["review","generation"]
    assert all(r.state=="completed" and r.turn_id and r.token_status=="unknown" and r.token_snapshot is None for r in first)
    assert first[0].image_items=={} and first[1].image_items=={"image":"completed"}
    assert await worker.run_once() is None
    assert [r.id for r in await rows(factory,opid)]==[r.id for r in first]
    async with factory() as db:
        w=await db.get(Wallet,owner);assert (w.balance,w.reserved)==(4,0)
        result=await summary(db,opid)
        assert result["turn_tokens"] is None and result["provider_model_requests"] is None
        assert result["attempts"][1]["image_coverage"]=="through_terminal"

@pytest.mark.parametrize("mode,states",[("rejected",["completed"]),("unknown_start",["completed","dispatch_intent"]),("crash_after_image",["completed","started"]),("clarification",["completed","completed"])])
async def test_failure_and_clarification_observations_do_not_guess_terminal_or_billable_counts(factory,tmp_path,mode,states):
    worker,_,owner,_,opid=await prepared(factory,tmp_path,mode)
    await worker.run_once();observed=await rows(factory,opid)
    assert [r.state for r in observed]==states
    assert all(r.token_status=="unknown" for r in observed)
    if mode=="unknown_start":assert observed[-1].turn_id is None
    if mode=="crash_after_image":assert observed[-1].image_items=={"image":"completed"}
    if mode=="clarification":
        async with factory() as db:
            assert (await db.get(Operation,opid)).state=="awaiting_input"
            w=await db.get(Wallet,owner);assert (w.balance,w.reserved)==(5,0)

async def test_usage_replaces_snapshots_and_filters_raw_fields_in_both_phases(factory,tmp_path,monkeypatch):
    worker,_,_,_,opid=await prepared(factory,tmp_path)
    original=ScriptedRpc.request
    async def inject(self,method,params):
        response=await original(self,method,params)
        if method=="turn/start":
            v=usage();v["secret"]="private";v["last"]["prompt"]="private"
            frame={"method":"thread/tokenUsage/updated","params":{"threadId":self.thread,"turnId":self.turn,"tokenUsage":v}}
            self.events[0:0]=[frame,copy.deepcopy(frame)]
        return response
    monkeypatch.setattr(ScriptedRpc,"request",inject)
    await worker.run_once()
    observed=await rows(factory,opid);assert len(observed)==2
    assert all(r.token_status=="observed" and r.token_snapshot["total"]["inputTokens"]==10 for r in observed)
    assert "private" not in repr([r.token_snapshot for r in observed])

async def test_stale_fence_cannot_modify_observations(factory,tmp_path):
    worker,_,_,_,opid=await prepared(factory,tmp_path)
    async with factory.begin() as db:op=await claim(db,worker.worker_id);token=op.fencing_token
    receive=observer(worker,opid,token,"generation","test-model")
    await receive({"kind":"dispatch","thread_id":"thread"})
    async with factory.begin() as db:(await db.get(Operation,opid)).fencing_token+=1
    with pytest.raises(ApplicationError,match="stale_lease"):
        await receive({"kind":"started","thread_id":"thread","turn_id":"turn"})
    assert (await rows(factory,opid))[0].state=="dispatch_intent"

async def test_admin_projection_is_private_and_missing_legacy_usage_is_unknown(api):
    client,factory,owner,_,branch=api
    from .test_operations import quoted,submit_request
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    assert (await client.get("/api/admin/v2/operations/"+opid)).status_code==403
    ordinary=(await client.get("/api/v2/operations/"+opid)).json();assert "runtime_usage" not in ordinary
    async with factory.begin() as db:(await db.get(User,owner)).role=UserRole.admin
    result=await client.get("/api/admin/v2/operations/"+opid)
    assert result.status_code==200 and result.headers["cache-control"]=="private, no-store"
    output=result.json()["runtime_usage"]
    assert output=={"status":"unknown","turn_tokens":None,"provider_model_requests":None,"amount":None,"attempts":[]}

async def test_duplicate_events_and_identity_conflicts_never_overwrite_receipts(factory,tmp_path):
    worker,_,_,_,opid=await prepared(factory,tmp_path)
    async with factory.begin() as db:op=await claim(db,worker.worker_id);token=op.fencing_token
    receive=observer(worker,opid,token,"generation","model")
    base={"thread_id":"thread","turn_id":"turn"}
    await receive({"kind":"dispatch","thread_id":"thread"})
    await receive({"kind":"started",**base})
    for _ in range(2):await receive({"kind":"image",**base,"item_id":"img","status":"completed"})
    with pytest.raises(ApplicationError,match="observation_identity_conflict"):
        await receive({"kind":"image",**base,"item_id":"img","status":"failed"})
    with pytest.raises(ApplicationError,match="observation_identity_conflict"):
        await receive({"kind":"started",**base,"turn_id":"foreign"})
    await receive({"kind":"usage",**base,"token_usage":usage()})
    await receive({"kind":"usage",**base,"token_usage":{"secret":"private"}})
    for _ in range(2):await receive({"kind":"completed",**base,"status":"failed"})
    row=(await rows(factory,opid))[0]
    assert row.image_items=={"img":"completed"} and row.state=="failed"
    assert row.token_status=="invalid" and row.token_snapshot is None
    with pytest.raises(ApplicationError,match="observation_already_completed"):
        await receive({"kind":"usage",**base,"token_usage":usage()})

async def test_admin_observed_data_excludes_runtime_ids_and_preserves_unknown_actual_costs(api,tmp_path):
    from .test_operations import quoted,submit_request
    from .test_worker import ScriptedRuntime
    from app.operations.worker import LocalWorker
    from app.core.config import Settings
    client,factory,owner,_,branch=api
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    settings=Settings(_env_file=None,app_env="test",v2_enabled=True,v2_new_operations=True,
        v2_runtime_mode="local",v2_local_user_id=owner,v2_runtime_dir=str(tmp_path/"runtime"),v2_storage_dir=str(tmp_path/"assets"))
    worker=LocalWorker(factory,settings,runtime=ScriptedRuntime("image"))
    assert await worker.run_once()==opid
    async with factory.begin() as db:(await db.get(User,owner)).role=UserRole.admin
    result=(await client.get("/api/admin/v2/operations/"+opid)).json()["runtime_usage"]
    assert len(result["attempts"])==2
    for attempt in result["attempts"]:
        assert "thread_id" not in attempt and "turn_id" not in attempt and "image_items" not in attempt
        assert attempt["image_tool_calls"] is None
    counts=(await client.get("/api/admin/v2/overview")).json()["runtime_usage"]
    assert counts=={"dispatch_intents":2,"acknowledged_attempts":2,"reported_snapshots":0,"missing_snapshots":2,
        "invalid_snapshots":0,"operations_without_observations":0,"turn_tokens":None,"provider_model_requests":None,"amount":None}
