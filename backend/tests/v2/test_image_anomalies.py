import copy
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.codex_runtime.rpc import RpcError
from app.core.time import utc_now
from app.design.models import RuntimeObservation, Operation, Wallet, Revision
from app.operations.observations import summary
from app.admin.image_alerts import duplicate_outputs
from .test_session import FakeRpc,session,item,completed
from .test_worker import prepared,ScriptedRpc
from .test_operations import factory
from .test_migrations import migration_database

@pytest.mark.parametrize("conflict",[False,True])
async def test_protocol_records_duplicate_identity_without_redelivery(conflict):
    original=item("imageGeneration",status="completed",result="private-image")
    duplicate=copy.deepcopy(original)
    if conflict:duplicate["params"]["item"]["result"]="different-private-image"
    rpc=FakeRpc([{"turn":{"id":"turn"}}],[original,duplicate,completed()])
    observations=[];delivered=[]
    async def observe(value):observations.append(value)
    async def image(turn,value):delivered.append(value)
    if conflict:
        with pytest.raises(RpcError,match="image_identity_conflict"):
            await session(rpc).run([],on_observation=observe,on_image=image)
    else:await session(rpc).run([],on_observation=observe,on_image=image)
    assert len(delivered)==1 and len(rpc.calls)==1
    assert [v for v in observations if v["kind"]=="image_duplicate"]==[
        {"kind":"image_duplicate","thread_id":"thread","turn_id":"turn","item_id":"imageGeneration","conflict":conflict}]
    assert "private-image" not in repr(observations)

@pytest.mark.parametrize("mode,expected",[
    ("replay",{"replayed_items":1,"conflicting_items":0,"extra_completed_items":0}),
    ("conflict",{"replayed_items":0,"conflicting_items":1,"extra_completed_items":0}),
    ("extra",{"replayed_items":0,"conflicting_items":0,"extra_completed_items":1}),
])
async def test_worker_persists_anomalies_without_second_delivery(factory,tmp_path,monkeypatch,mode,expected):
    worker,_,owner,_,opid=await prepared(factory,tmp_path)
    original=ScriptedRpc.request
    async def injected(self,method,params):
        response=await original(self,method,params)
        if method=="turn/start" and self.images:
            extra=copy.deepcopy(self.events[0])
            if mode=="conflict":extra["params"]["item"]["result"]="invalid-extra-private-data"
            if mode=="extra":extra["params"]["item"]["id"]="second-image"
            self.events.insert(1,extra)
            if mode=="replay":self.events.insert(2,copy.deepcopy(extra))
        return response
    monkeypatch.setattr(ScriptedRpc,"request",injected)
    await worker.run_once()
    async with factory() as db:
        result=await summary(db,opid);main=next(a for a in result["attempts"] if a["phase"]=="generation")
        assert main["image_anomalies"]==expected
        assert (await db.get(Wallet,owner)).balance==4
        assert len(list(await db.scalars(select(Revision).where(Revision.operation_id==opid))))==1
        report=await duplicate_outputs(db,utc_now()-timedelta(hours=1))
        assert report["count"]==(0 if mode=="replay" else 1)
        assert report["operation_ids"]==([] if mode=="replay" else [opid])
        assert "private-data" not in repr(result)

async def test_missing_old_observations_are_unknown_not_clear(factory,tmp_path):
    worker,_,_,_,opid=await prepared(factory,tmp_path)
    async with factory() as db:assert (await duplicate_outputs(db,utc_now()-timedelta(hours=1)))["count"] is None
    await worker.run_once()
    async with factory.begin() as db:
        row=await db.scalar(select(RuntimeObservation).where(RuntimeObservation.operation_id==opid,RuntimeObservation.phase=="generation"))
        row.image_anomalies=None
    async with factory() as db:
        result=await summary(db,opid)
        assert result["attempts"][-1]["image_anomalies"] is None
        assert (await duplicate_outputs(db,utc_now()-timedelta(hours=1)))["count"] is None
