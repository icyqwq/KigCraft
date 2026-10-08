"""S3 protocol and application components. No MinIO server, network or model calls."""
import asyncio
import base64
import hashlib
import io
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from sqlalchemy import func, select

from app.assets import repository as store
from app.assets.storage import upload_in_transaction, asset_path
from app.core.config import Settings
from app.design.models import Asset, Operation, Revision, Wallet
from app.design.service import ApplicationError
from app.operations.worker import LocalWorker
from .test_operations import factory, setup, quoted, submit_request, request
from .test_migrations import migration_database
from .test_api import api
from .test_worker import ScriptedRuntime, png


class Objects:
    def __init__(self):
        self.objects={}
        self.calls=[]
        self.fail_get=False
        self.lock=threading.RLock()
    def put_object(self,**values):
        with self.lock:
            self.calls.append(("put",values))
            assert values["IfNoneMatch"]=="*"
            key=values["Key"]
            if key in self.objects:
                raise ClientError({"ResponseMetadata":{"HTTPStatusCode":412},"Error":{"Code":"PreconditionFailed"}},"PutObject")
            self.objects[key]=bytes(values["Body"])
        return {}
    def get_object(self,**values):
        with self.lock:
            self.calls.append(("get",values))
            if self.fail_get or values["Key"] not in self.objects:
                raise OSError("upstream diagnostic must not escape")
            data=self.objects[values["Key"]]
            return {"ContentLength":len(data),"Body":StreamingBody(io.BytesIO(data),len(data))}


def config(tmp_path,**overrides):
    return Settings(_env_file=None,v2_enabled=True,app_env="test",v2_asset_backend="minio",
        v2_storage_dir=str(tmp_path/"cache"),v2_s3_endpoint_url="http://minio.invalid:9000",
        v2_s3_bucket="private-assets",v2_s3_access_key_id="synthetic-access",
        v2_s3_secret_access_key="synthetic-secret",**overrides)


def bind(monkeypatch,cfg,objects):
    monkeypatch.setattr(store,"get_settings",lambda:cfg)
    monkeypatch.setattr(store,"make_client",lambda settings:objects)
    import app.design.router as router
    monkeypatch.setattr(router,"get_settings",lambda:cfg)


def test_pinned_sdk_supports_conditional_put_and_actual_readback(tmp_path):
    cfg=config(tmp_path)
    client=store.make_client(cfg)
    data=b"private bytes";sha=hashlib.sha256(data).hexdigest()
    params={"Bucket":cfg.v2_s3_bucket,"Key":"v2/uploads/test.png","Body":data,"ContentLength":len(data),
        "ContentType":"image/png","IfNoneMatch":"*",
        "ContentMD5":base64.b64encode(hashlib.md5(data,usedforsecurity=False).digest()).decode(),
        "Metadata":{"sha256":sha}}
    with Stubber(client) as stub:
        stub.add_response("put_object",{},params)
        stub.add_response("get_object",{"ContentLength":len(data),"Body":StreamingBody(io.BytesIO(data),len(data))},
            {"Bucket":cfg.v2_s3_bucket,"Key":"v2/uploads/test.png"})
        store.MinioStore(cfg,client=client).put("uploads/test.png",data,sha,"image/png")
        stub.assert_no_pending_responses()
    client.close()


def test_immutable_retry_checks_bytes_and_warm_cache_cannot_hide_remote_loss(tmp_path):
    cfg=config(tmp_path);objects=Objects();repo=store.MinioStore(cfg,client=objects)
    data=b"abc";sha=hashlib.sha256(data).hexdigest();key="masters/test.png"
    repo.put(key,data,sha,"image/png")
    repo.put(key,data,sha,"image/png")
    asset=SimpleNamespace(object_key=key,sha256=sha,byte_size=len(data))
    assert repo.materialize(asset).read_bytes()==data
    objects.objects["v2/"+key]=b"xyz"
    with pytest.raises(ApplicationError,match="asset_identity_conflict"):
        repo.put(key,data,sha,"image/png")
    objects.objects.clear()
    with pytest.raises(store.StorageUnavailable,match="^asset_storage_unavailable$"):
        repo.materialize(asset)
    assert (Path(cfg.v2_storage_dir)/key).read_bytes()==data


@pytest.mark.parametrize("field,value",[
    ("v2_s3_secret_access_key",""),("v2_s3_bucket",""),
    ("v2_s3_endpoint_url","http://user:secret@host"),("v2_s3_endpoint_url","https://host/bucket"),
    ("v2_s3_prefix","../private"),("v2_s3_prefix","v2/"),
])
def test_invalid_config_never_creates_network_client(tmp_path,monkeypatch,field,value):
    cfg=config(tmp_path);setattr(cfg,field,value)
    monkeypatch.setattr(store,"make_client",lambda *_:pytest.fail("network client should not be created"))
    with pytest.raises(ApplicationError,match="asset_storage_not_configured"):
        store.MinioStore(cfg)


async def test_cancelled_write_is_drained_before_transaction_can_release():
    entered=threading.Event();release=threading.Event();finished=threading.Event()
    def write():
        entered.set()
        assert release.wait(5)
        finished.set()
    task=asyncio.create_task(store.storage_io(write))
    assert await asyncio.to_thread(entered.wait,5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not finished.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_upload_download_authorization_and_worker_read_from_remote_without_local_source(factory,tmp_path,monkeypatch):
    cfg=config(tmp_path);objects=Objects();bind(monkeypatch,cfg,objects)
    owner,_,branch=await setup(factory)
    other,_,_=await setup(factory)
    async with factory.begin() as db:
        asset=await upload_in_transaction(db,base64.b64decode(png()),owner,Path(cfg.v2_storage_dir))
        db.add(asset)
    assert len(objects.objects)==1 and not Path(cfg.v2_storage_dir).exists()
    from app.design.router import image
    before=len(objects.calls)
    async with factory() as db:
        with pytest.raises(ApplicationError,match="not_found"):
            await image(asset.id,SimpleNamespace(id=other),db)
    assert len(objects.calls)==before
    async with factory() as db:
        response=await image(asset.id,SimpleNamespace(id=owner),db)
        assert Path(response.path).read_bytes()==objects.objects["v2/"+asset.object_key]
        assert response.headers["cache-control"]=="private, no-store"
    payload=request(attachments=[{"asset_id":asset.id,"usage":"wig"}])
    operation=await submit_request(factory,owner,branch,await quoted(factory,owner,branch,payload))
    cfg.v2_runtime_mode="local";cfg.v2_local_user_id=owner
    cfg.v2_runtime_dir=str(tmp_path/"runtime")
    runtime=ScriptedRuntime()
    worker=LocalWorker(factory,cfg,runtime=runtime)
    assert await worker.run_once()==operation
    async with factory() as db:
        assert (await db.get(Operation,operation)).state=="succeeded"
        assert (await db.get(Wallet,owner)).balance==4
    assert any(key.startswith("v2/masters/") for key in objects.objects)


async def test_readback_failure_leaves_publication_retry_and_no_charge_then_retries_same_object(factory,tmp_path,monkeypatch):
    cfg=config(tmp_path);objects=Objects();bind(monkeypatch,cfg,objects)
    owner,_,branch=await setup(factory)
    operation=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    cfg.v2_runtime_mode="local";cfg.v2_local_user_id=owner
    cfg.v2_runtime_dir=str(tmp_path/"runtime")
    runtime=ScriptedRuntime()
    worker=LocalWorker(factory,cfg,runtime=runtime)
    objects.fail_get=True
    assert await worker.run_once()==operation
    async with factory() as db:
        op=await db.get(Operation,operation)
        assert op.state=="publishing" and op.staged_outputs and op.upstream_status=="completed"
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,1)
        assert await db.scalar(select(func.count()).select_from(Revision))==0
    assert len(objects.objects)==1
    model_calls=len(runtime.calls)
    objects.fail_get=False
    assert await worker.retry_publish(operation)=="succeeded"
    assert len(runtime.calls)==model_calls and len(objects.objects)==1
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(4,0)
        assert await db.scalar(select(func.count()).select_from(Revision))==1


async def test_editor_checkpoint_reads_minio_and_preserves_revision_and_wallet(factory,tmp_path,monkeypatch):
    from .test_editor_save import fixture as editor_fixture, save
    owner,branch,revision,root,payload=await editor_fixture(factory,tmp_path)
    cfg=config(tmp_path);objects=Objects()
    objects.objects["v2/"+root.object_key]=(tmp_path/root.object_key).read_bytes()
    bind(monkeypatch,cfg,objects)
    assert not Path(cfg.v2_storage_dir).exists()
    disposition,checkpoint,result=await save(factory,owner,branch,payload,Path(cfg.v2_storage_dir))
    assert disposition=="checkpoint" and result.id==revision.id
    assert checkpoint.clean_asset_id==root.id
    assert (Path(cfg.v2_storage_dir)/root.object_key).is_file()
    assert [method for method,_ in objects.calls]==["get"]
    async with factory() as db:
        assert (await db.get(Wallet,owner)).balance==4
        assert await db.scalar(select(func.count()).select_from(Revision))==1


def test_minio_capacity_does_not_claim_cache_disk_is_bucket_capacity(tmp_path,monkeypatch):
    from app.admin import v2_health
    monkeypatch.setattr(v2_health.shutil,"disk_usage",lambda *_:pytest.fail("cache is not bucket capacity"))
    signal=v2_health.storage_signal(config(tmp_path))
    assert signal["status"]=="unknown" and signal["free_bytes"] is None and signal["scope"]=="minio"


async def test_http_failure_is_sanitized_and_no_ready_asset_commits(api,tmp_path,monkeypatch):
    from app.core.config import get_settings
    client,factory,owner,_,_=api
    cfg=config(tmp_path)
    cfg.v2_storage_dir=get_settings().v2_storage_dir
    objects=Objects();objects.fail_get=True
    monkeypatch.setattr(store,"get_settings",lambda:cfg)
    monkeypatch.setattr(store,"make_client",lambda _:objects)
    response=await client.post("/api/v2/assets/uploads",
        files={"file":("ref.png",base64.b64decode(png()),"image/png")})
    assert response.status_code==503
    assert response.json()=={"code":"asset_storage_unavailable","retry_action":"retry"}
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Asset))==0
    assert len(objects.objects)==1
