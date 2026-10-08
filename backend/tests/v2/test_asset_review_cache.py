import base64
import json
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.assets.storage import upload_asset
from app.contracts.v2 import InputSnapshot
from app.core.config import Settings
from app.core.time import utc_now
from app.design.models import Asset, AssetReviewCache, Branch, InputRecord, Operation, Wallet
from app.design.service import ApplicationError
from app.moderation.cache import policy_key, cached_assets, missing_assets
from app.operations.worker import LocalWorker
from .test_operations import factory, setup, request, quoted, submit_request
from .test_migrations import migration_database
from .test_worker import ScriptedRuntime, ScriptedRpc, png


async def fixture(factory, tmp_path):
    owner, design, branch = await setup(factory)
    settings = Settings(_env_file=None, app_env="test", v2_enabled=True, v2_new_operations=True,
        v2_runtime_mode="local", v2_local_user_id=owner,
        v2_runtime_dir=str(tmp_path/"runtime"), v2_storage_dir=str(tmp_path/"assets"))
    asset = upload_asset(base64.b64decode(png()), owner, tmp_path/"assets")
    async with factory.begin() as db:
        db.add(asset)
    runtime = ScriptedRuntime("rejected")
    worker = LocalWorker(factory, settings, runtime=runtime)
    return owner, branch, asset, worker, runtime


async def send(factory, owner, branch, asset, identity, usage):
    async with factory() as db:
        current = await db.get(Branch, branch)
    payload = request(client_request_id=identity, text="Current request "+identity,
        expected_branch_version=current.state_version, base_revision_id=current.current_revision_id,
        attachments=[{"asset_id":asset.id,"usage":usage}])
    return await submit_request(factory, owner, branch, await quoted(factory, owner, branch, payload))


def review_inputs(runtime):
    return [params for images, method, params in runtime.calls if not images and method=="turn/start"]


async def test_cache_survives_worker_restart_but_changed_usage_is_reviewed_again(factory, tmp_path):
    owner, branch, asset, worker, runtime = await fixture(factory, tmp_path)
    first = await send(factory, owner, branch, asset, "first-review", "color reference")
    await worker.run_once()
    async with factory() as db:
        rows = list(await db.scalars(select(AssetReviewCache)))
        assert len(rows)==1 and rows[0].verdict=="allow" and rows[0].source_operation_id==first
        assert (await db.get(Operation, first)).error_code=="moderation_rejected"
    restarted = LocalWorker(factory, worker.settings, runtime=runtime)
    second = await send(factory, owner, branch, asset, "second-review", "different intended use")
    await restarted.run_once()
    calls = review_inputs(runtime)
    assert len(calls)==2
    a,b = [json.loads(p["input"][0]["text"]) for p in calls]
    assert a["asset_checks"]==[1] and a["cached_asset_decisions"]==[]
    assert b["asset_checks"]==[] and b["cached_asset_decisions"]==[{"ordinal":1,"verdict":"allow"}]
    assert b["user_text"]=="Current request second-review"
    assert b["references"][0]["usage"]=="different intended use"
    assert all(any(i["type"]=="localImage" for i in p["input"]) for p in calls)
    assert not any(images for images,_,_ in runtime.calls)
    async with factory() as db:
        assert (await db.get(Operation, second)).error_code=="moderation_rejected"
        wallet=await db.get(Wallet, owner)
        assert (wallet.balance,wallet.reserved)==(5,0)
    runtime.mode="image"
    third = await send(factory, owner, branch, asset, "third-review", "ordinary costume")
    await restarted.run_once()
    assert len(review_inputs(runtime))==3
    assert len([c for c in runtime.calls if c[0] and c[1]=="turn/start"])==1
    async with factory() as db:
        assert (await db.get(Operation, third)).state=="succeeded"
        assert (await db.get(Wallet, owner)).balance==4
        assert await db.scalar(select(func.count()).select_from(AssetReviewCache))==1


@pytest.mark.parametrize("change", ["owner","policy","hash","access"])
async def test_cache_key_isolation_and_permissions(factory,tmp_path,change):
    owner,branch,asset,worker,runtime=await fixture(factory,tmp_path)
    opid=await send(factory,owner,branch,asset,"seed-cache","colors")
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        snapshot=InputSnapshot.model_validate((await db.get(InputRecord,op.input_snapshot_id)).snapshot)
        row=await db.scalar(select(AssetReviewCache))
        policy=row.policy_key
    if change=="owner":
        other,_,_=await setup(factory)
        other_asset=upload_asset(base64.b64decode(png()),other,tmp_path/"assets")
        async with factory.begin() as db: db.add(other_asset)
        assert other_asset.sha256==asset.sha256
        owner=other
        snapshot=snapshot.model_copy(update={"references":(snapshot.references[0].model_copy(update={"asset_id":other_asset.id}),)})
    elif change=="policy": policy="b"*64
    elif change=="hash":
        async with factory.begin() as db:
            (await db.get(Asset,asset.id)).sha256="c"*64
        snapshot=snapshot.model_copy(update={"references":(snapshot.references[0].model_copy(update={"sha256":"c"*64}),)})
    else:
        other,_,_=await setup(factory)
        async with factory() as db:
            with pytest.raises(ApplicationError,match="input_asset_unavailable"):
                await cached_assets(db,other,snapshot,policy)
        return
    async with factory() as db:
        assert await cached_assets(db,owner,snapshot,policy)=={}


@pytest.mark.parametrize("failure",["offline","missing","uncertain"])
async def test_failed_or_uncertain_asset_review_never_creates_allow_cache(factory,tmp_path,monkeypatch,failure):
    owner,branch,asset,worker,runtime=await fixture(factory,tmp_path)
    original=ScriptedRpc.request
    async def altered(self,method,params):
        if not self.images and method=="turn/start" and failure=="offline":
            from app.codex_runtime.rpc import RpcClosed
            raise RpcClosed()
        result=await original(self,method,params)
        if not self.images and method=="turn/start":
            text=self.events[0]["params"]["item"]["text"]
            value=json.loads(text)
            if failure=="missing": value["asset_decisions"]=[]
            else: value["asset_decisions"][0]["verdict"]="uncertain"
            self.events[0]["params"]["item"]["text"]=json.dumps(value)
        return result
    monkeypatch.setattr(ScriptedRpc,"request",altered)
    opid=await send(factory,owner,branch,asset,"failed-review","colors")
    await worker.run_once()
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AssetReviewCache))==0
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,0)
        expected="moderation_rejected" if failure=="uncertain" else "moderation_unavailable"
        assert (await db.get(Operation,opid)).error_code==expected
    assert not any(c[0] for c in runtime.calls)


async def test_cache_owner_source_integrity_is_enforced_by_database(factory,tmp_path):
    owner,branch,asset,worker,runtime=await fixture(factory,tmp_path)
    opid=await send(factory,owner,branch,asset,"review-source","colors")
    other,_,_=await setup(factory)
    with pytest.raises(IntegrityError):
        async with factory.begin() as db:
            db.add(AssetReviewCache(owner_id=other,sha256=asset.sha256,policy_key="a"*64,
                verdict="allow",source_operation_id=opid,created_at=utc_now()))


def test_policy_invalidates_on_model_runtime_version_and_rule_changes(monkeypatch):
    import app.moderation.cache as cache
    arguments={"model":"review","codex_version":"version","runtime_identity":"identity"}
    initial=policy_key(**arguments)
    for name in arguments:
        assert policy_key(**(arguments|{name:"changed"}))!=initial
    monkeypatch.setattr(cache,"ASSET_INSTRUCTIONS",cache.ASSET_INSTRUCTIONS+" Changed rule.")
    assert policy_key(**arguments)!=initial
