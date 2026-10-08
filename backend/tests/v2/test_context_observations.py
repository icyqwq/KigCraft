import pytest
from sqlalchemy import select
from app.codex_runtime.rpc import RpcClosed
from app.design.models import ContextObservation, Operation
from app.design.service import ApplicationError
from app.operations.context_observations import open_context, totals
from app.operations.leases import claim
from .test_worker import prepared
from .test_operations import factory
from .test_migrations import migration_database


@pytest.mark.parametrize("thread,fork,method",[(None,None,"start"),("existing",None,"resume"),("existing","turn","fork")])
async def test_real_session_validation_records_each_open_kind(factory,tmp_path,thread,fork,method):
    worker,runtime,_,_,opid=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        op=await claim(db,worker.worker_id);token=op.fencing_token
    async with runtime.connection(tmp_path,images=True) as rpc:
        await open_context(worker,rpc,opid,token,thread_id=thread,fork_after=fork)
        with pytest.raises(ApplicationError,match="context_open_already_observed"):
            await open_context(worker,rpc,opid,token,thread_id=thread,fork_after=fork)
    assert len(runtime.calls)==1
    async with factory() as db:
        row=await db.scalar(select(ContextObservation))
        assert row.method==method and row.state=="confirmed" and row.completed_at
        result=await totals(db)
        assert result["methods"][method]=={"attempts":1,"confirmed":1,"unconfirmed":0,"confirmation_rate":1}
        for kind in {"start","resume","fork"}-{method}:
            assert result["methods"][kind]["confirmation_rate"] is None


@pytest.mark.parametrize("mode",["disconnect","wrong_model","wrong_thread","stale"])
async def test_uncertain_or_invalid_or_stale_response_is_not_success(factory,tmp_path,mode):
    worker,runtime,_,_,opid=await prepared(factory,tmp_path)
    async with factory.begin() as db:
        op=await claim(db,worker.worker_id);token=op.fencing_token
    async with runtime.connection(tmp_path,images=True) as rpc:
        original=rpc.request
        async def respond(method,params):
            result=await original(method,params)
            if mode=="disconnect":raise RpcClosed()
            if mode=="wrong_model":result["model"]="wrong"
            if mode=="wrong_thread":result["thread"]["id"]="foreign"
            if mode=="stale":
                async with factory.begin() as db:
                    (await db.get(Operation,opid)).fencing_token+=1
            return result
        rpc.request=respond
        with pytest.raises(Exception):
            await open_context(worker,rpc,opid,token,thread_id="existing")
    async with factory() as db:
        row=await db.scalar(select(ContextObservation))
        assert row.state==("sent" if mode=="stale" else "unconfirmed")
        assert (await totals(db))["methods"]["resume"]=={
            "attempts":1,"confirmed":0,"unconfirmed":1,"confirmation_rate":0}


async def test_worker_wires_main_open_only_and_empty_counts_are_unknown(factory,tmp_path):
    worker,_,_,_,opid=await prepared(factory,tmp_path)
    async with factory() as db:
        result=await totals(db)
        assert result["since"] is None
        assert all(x["confirmation_rate"] is None for x in result["methods"].values())
    await worker.run_once()
    async with factory.begin() as db:
        rows=list(await db.scalars(select(ContextObservation)))
        assert len(rows)==1 and rows[0].method=="start" and rows[0].state=="confirmed"
        # Older dispatched operations cannot be retrospectively called successful.
        await db.delete(rows[0])
    async with factory() as db:
        result=await totals(db)
        assert result["dispatched_operations_without_observations"]==1
        assert result["methods"]["resume"]["confirmation_rate"] is None
