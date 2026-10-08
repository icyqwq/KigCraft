import base64
import json
from sqlalchemy import select, func
from app.assets.storage import upload_asset
from app.codex_runtime.rpc import RpcClosed
from app.design.models import AssetReviewCache, Operation, Wallet
from .test_asset_review_cache import fixture, send, review_inputs
from .test_migrations import migration_database
from .test_operations import factory, request, quoted, submit_request
from .test_worker import ScriptedRpc, png


async def test_cache_hit_does_not_allow_request_during_review_outage(factory,tmp_path,monkeypatch):
    owner,branch,asset,worker,runtime=await fixture(factory,tmp_path)
    first=await send(factory,owner,branch,asset,"seed-hit","colors")
    await worker.run_once()
    original=ScriptedRpc.request
    async def offline(self,method,params):
        if not self.images and method=="turn/start":
            self.runtime.calls.append((False,method,params))
            raise RpcClosed()
        return await original(self,method,params)
    monkeypatch.setattr(ScriptedRpc,"request",offline)
    second=await send(factory,owner,branch,asset,"outage-hit","changed usage")
    await worker.run_once()
    payload=json.loads(review_inputs(runtime)[-1]["input"][0]["text"])
    assert payload["cached_asset_decisions"]==[{"ordinal":1,"verdict":"allow"}]
    assert payload["asset_checks"]==[]
    assert not any(c[0] for c in runtime.calls)
    async with factory() as db:
        assert (await db.get(Operation,second)).error_code=="moderation_unavailable"
        row=await db.scalar(select(AssetReviewCache))
        assert row.source_operation_id==first and row.verdict=="allow"
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,0)


async def test_same_bytes_with_two_asset_ids_produces_one_stable_assessment(factory,tmp_path):
    owner,branch,asset,worker,runtime=await fixture(factory,tmp_path)
    duplicate=upload_asset(base64.b64decode(png()),owner,tmp_path/"assets")
    async with factory.begin() as db: db.add(duplicate)
    assert duplicate.id!=asset.id and duplicate.sha256==asset.sha256
    payload=request(attachments=[{"asset_id":asset.id,"usage":"color"},{"asset_id":duplicate.id,"usage":"material"}])
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch,payload))
    await worker.run_once()
    inputs=review_inputs(runtime)[0]["input"]
    assert json.loads(inputs[0]["text"])["asset_checks"]==[1]
    assert sum(i["type"]=="localImage" for i in inputs)==2
    async with factory() as db:
        assert (await db.get(Operation,opid)).error_code=="moderation_rejected"
        assert await db.scalar(select(func.count()).select_from(AssetReviewCache))==1
