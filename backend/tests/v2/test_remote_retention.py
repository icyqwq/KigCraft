"""Remote lifecycle components with fake S3 and isolated PostgreSQL. No real deletion."""
from datetime import timedelta
import hashlib
import io
from pathlib import Path

from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber
import pytest

from app.assets import repository
from app.admin.v2_controls import set_control
from app.design.models import Asset, InputRecord
from app.design.service import ApplicationError
from app.maintenance import upload_retention as uploads, trash_assets as trash
from app.maintenance.remote_assets import RemoteAssets
from app.maintenance.orphan_assets import CleanupError
from .test_minio_assets import config
from .test_upload_retention import prepared as upload_prepared
from .test_trash_assets import prepared as trash_prepared, shared_design
from .test_operations import factory
from .test_migrations import migration_database


def missing(code="NoSuchKey"):
    return ClientError({"Error":{"Code":code},"ResponseMetadata":{"HTTPStatusCode":404}},"Object")


class Objects:
    def __init__(self,mode,key,data):
        self.mode=mode; self.key=key; self.calls=[]; self.fail_after_delete=False; self.truncated=False
        self.versions = {None:data} if mode=="unversioned" else {"old":data,"null" if mode=="Suspended" else "current":data}
        self.markers=[]
    def get_bucket_versioning(self,**args):
        self.calls.append(("versioning",args))
        return {} if self.mode=="unversioned" else {"Status":self.mode}
    def list_object_versions(self,**args):
        self.calls.append(("list",args))
        assert args["Prefix"]==self.key
        return {"IsTruncated":self.truncated,"Versions":[
            {"Key":self.key,"VersionId":key,"Size":len(data),"IsLatest":index==len(self.versions)-1}
            for index,(key,data) in enumerate(self.versions.items())],"DeleteMarkers":self.markers}
    def get_object(self,**args):
        self.calls.append(("get",args))
        if args["Key"]!=self.key:raise missing()
        version=args.get("VersionId",next(reversed(self.versions),None))
        if version not in self.versions:raise missing("NoSuchVersion" if "VersionId" in args else "NoSuchKey")
        data=self.versions[version]
        result={"Body":StreamingBody(io.BytesIO(data),len(data)),"ContentLength":len(data),
                "ETag":'"'+hashlib.md5(data,usedforsecurity=False).hexdigest()+'"'}
        if version is not None:result["VersionId"]=version
        return result
    def delete_object(self,**args):
        self.calls.append(("delete",args))
        assert args["Key"]==self.key and "BypassGovernanceRetention" not in args
        version=args.get("VersionId")
        if version not in self.versions:raise missing("NoSuchVersion")
        expected='"'+hashlib.md5(self.versions[version],usedforsecurity=False).hexdigest()+'"'
        if args.get("IfMatch")!=expected:
            raise ClientError({"Error":{"Code":"PreconditionFailed"},"ResponseMetadata":{"HTTPStatusCode":412}},"DeleteObject")
        del self.versions[version]
        if self.fail_after_delete:
            self.fail_after_delete=False
            raise OSError("simulated lost delete response")
        return {}


def bind(settings,tmp_path,monkeypatch,asset,root,mode="unversioned"):
    cfg=config(tmp_path)
    for field in ("v2_asset_backend","v2_s3_endpoint_url","v2_s3_bucket","v2_s3_prefix",
                  "v2_s3_access_key_id","v2_s3_secret_access_key","v2_s3_region"):
        setattr(settings,field,getattr(cfg,field))
    settings.v2_enabled=True;settings.app_env="test";settings.v2_runtime_mode="local"
    settings.v2_storage_dir=str(tmp_path/"missing-cache")
    objects=Objects(mode,"v2/"+asset.object_key,(root/asset.object_key).read_bytes())
    monkeypatch.setattr(repository,"make_client",lambda _:objects)
    return objects


async def inspect_upload(factory,root,cfg,now,action="expire",**kwargs):
    async with factory.begin() as db:
        return await uploads.inspect_uploads(db,root,cfg,action,now=now,writers_stopped=True,**kwargs)


async def apply_upload(factory,root,cfg,owner,now,report):
    async with factory.begin() as db:
        return await uploads.apply_uploads(db,root,cfg,report["action"],report["digest"],owner,
            "Reviewed remote policy",now=now,writers_stopped=True,batch_id=report["batch_id"])


@pytest.mark.parametrize("mode",["unversioned","Enabled","Suspended"])
async def test_remote_upload_expiry_grace_and_version_bytes_purge(factory,tmp_path,monkeypatch,mode):
    owner,_,_,asset,root,cfg,now=await upload_prepared(factory,tmp_path)
    objects=bind(cfg,tmp_path,monkeypatch,asset,root,mode)
    async with factory.begin() as db:await set_control(db,"global",True)
    report=await inspect_upload(factory,root,cfg,now)
    assert report["storage_root"].startswith("minio:") and report["remote_objects"][asset.id]["present"]
    await apply_upload(factory,root,cfg,owner,now,report)
    assert objects.versions and (root/asset.object_key).exists()
    assert not (await inspect_upload(factory,root,cfg,now+timedelta(days=1),"purge"))["candidates"]
    later=now+timedelta(days=8)
    report=await inspect_upload(factory,root,cfg,later,"purge")
    result=await apply_upload(factory,root,cfg,owner,later,report)
    assert result["count"]==1 and not objects.versions and (root/asset.object_key).exists()
    async with factory() as db:assert (await db.get(Asset,asset.id)).status=="purged"
    assert not Path(cfg.v2_storage_dir).exists()


async def test_upload_restore_preserves_remote_bytes_and_old_receipts_cannot_delete_new_target(factory,tmp_path,monkeypatch):
    owner,_,_,asset,root,cfg,now=await upload_prepared(factory,tmp_path)
    objects=bind(cfg,tmp_path,monkeypatch,asset,root)
    async with factory.begin() as db:await set_control(db,"global",True)
    report=await inspect_upload(factory,root,cfg,now)
    expired=await apply_upload(factory,root,cfg,owner,now,report)
    cfg.v2_s3_endpoint_url="http://other.invalid:9000"
    report=await inspect_upload(factory,root,cfg,now+timedelta(days=8),"purge")
    assert not report["candidates"] and report["retained"][0]["reason"]=="expiry_scope_unbound"
    cfg.v2_s3_endpoint_url="http://minio.invalid:9000"
    report=await inspect_upload(factory,root,cfg,now,"restore",batch_id=expired["receipt_id"])
    await apply_upload(factory,root,cfg,owner,now,report)
    assert objects.versions and not any(method=="delete" for method,_ in objects.calls)
    async with factory() as db:assert (await db.get(Asset,asset.id)).status=="ready"


async def test_failed_delete_response_keeps_expired_and_can_resume_without_reexposure(factory,tmp_path,monkeypatch):
    owner,_,_,asset,root,cfg,now=await upload_prepared(factory,tmp_path)
    objects=bind(cfg,tmp_path,monkeypatch,asset,root,"Enabled")
    async with factory.begin() as db:await set_control(db,"global",True)
    await apply_upload(factory,root,cfg,owner,now,await inspect_upload(factory,root,cfg,now))
    later=now+timedelta(days=8)
    report=await inspect_upload(factory,root,cfg,later,"purge")
    objects.fail_after_delete=True
    with pytest.raises(repository.StorageUnavailable):
        await apply_upload(factory,root,cfg,owner,later,report)
    async with factory() as db:assert (await db.get(Asset,asset.id)).status=="expired"
    assert len(objects.versions)==1
    report=await inspect_upload(factory,root,cfg,later,"purge")
    await apply_upload(factory,root,cfg,owner,later,report)
    assert not objects.versions


async def test_reference_added_after_expiry_protects_remote_upload(factory,tmp_path,monkeypatch):
    owner,_,branch,asset,root,cfg,now=await upload_prepared(factory,tmp_path)
    objects=bind(cfg,tmp_path,monkeypatch,asset,root)
    async with factory.begin() as db:await set_control(db,"global",True)
    await apply_upload(factory,root,cfg,owner,now,await inspect_upload(factory,root,cfg,now))
    async with factory.begin() as db:
        db.add(InputRecord(branch_id=branch,digest="a"*64,snapshot={"asset":asset.id}))
    report=await inspect_upload(factory,root,cfg,now+timedelta(days=8),"purge")
    assert not report["candidates"] and report["retained"][0]["reason"]=="referenced"
    assert objects.versions


async def test_remote_upload_requires_stop_pause_and_committed_expiry(factory,tmp_path,monkeypatch):
    owner,_,_,asset,root,cfg,now=await upload_prepared(factory,tmp_path)
    objects=bind(cfg,tmp_path,monkeypatch,asset,root)
    async with factory.begin() as db:
        with pytest.raises(CleanupError,match="writers_stopped"):
            await uploads.inspect_uploads(db,root,cfg)
    with pytest.raises(CleanupError,match="generation_must_be_paused"):
        await inspect_upload(factory,root,cfg,now)
    async with factory.begin() as db:await set_control(db,"global",True)
    report=await inspect_upload(factory,root,cfg,now)
    with pytest.raises(CleanupError,match="expiry_commit_required"):
        async with factory.begin() as db:
            await uploads.apply_uploads(db,root,cfg,"expire",report["digest"],owner,"Reviewed remote policy",
                                        now=now,writers_stopped=True)
            await uploads.inspect_uploads(db,root,cfg,"purge",now=now+timedelta(days=8),writers_stopped=True)
    assert objects.versions and not any(method=="delete" for method,_ in objects.calls)


async def test_remote_trash_production_configuration_shared_refs_and_purge(factory,tmp_path,monkeypatch):
    owner,revision,asset,cfg,now=await trash_prepared(factory,tmp_path)
    original=Path(cfg.v2_storage_dir)
    objects=bind(cfg,tmp_path,monkeypatch,asset,original,"Enabled")
    cfg.app_env="production";cfg.v2_runtime_mode="container";cfg.v2_container_user_ids=[owner]
    cfg.v2_container_image="runtime@sha256:"+"a"*64;cfg.v2_egress_image="egress@sha256:"+"b"*64
    cfg.v2_egress_hosts=["model.example.com"]
    async with factory.begin() as db:
        report=await trash.inspect_assets(db,cfg,writers_stopped=True,now=now)
        result=await trash.expire_assets(db,cfg,report["digest"],owner,"Reviewed remote trash",writers_stopped=True,now=now)
    async with factory.begin() as db:
        assert await trash.retired_asset(db,await db.get(Asset,asset.id))
    later=now+timedelta(days=2)
    async with factory.begin() as db:
        result=await trash.purge_assets(db,cfg,result["receipt_id"],owner,"Reviewed remote trash",
                                       writers_stopped=True,now=later)
    assert result["count"]==1 and not objects.versions and (original/asset.object_key).exists()


async def test_shared_trash_asset_is_retained_remotely(factory,tmp_path,monkeypatch):
    owner,revision,asset,cfg,now=await trash_prepared(factory,tmp_path)
    original=Path(cfg.v2_storage_dir)
    await shared_design(factory,owner,asset,now,"active")
    objects=bind(cfg,tmp_path,monkeypatch,asset,original)
    async with factory.begin() as db:report=await trash.inspect_assets(db,cfg,writers_stopped=True,now=now)
    assert not report["candidates"] and objects.versions and not objects.calls


@pytest.mark.parametrize("fault",["foreign_version","truncated","changed_current"])
def test_changed_or_incomplete_version_inventory_prevents_all_deletes(tmp_path,monkeypatch,fault):
    cfg=config(tmp_path);data=b"abc";sha=hashlib.sha256(data).hexdigest()
    objects=Objects("Enabled","v2/uploads/test.png",data)
    if fault=="foreign_version":objects.versions["old"]=b"xyz"
    elif fault=="truncated":objects.truncated=True
    else:objects.versions["current"]=b"xyz"
    monkeypatch.setattr(repository,"make_client",lambda _:objects)
    with pytest.raises((CleanupError,ApplicationError), match="remote_version_inventory|asset_identity_conflict"):
        RemoteAssets(cfg).delete("uploads/test.png",sha,len(data))
    assert not any(method=="delete" for method,_ in objects.calls)


def test_version_delete_markers_are_kept_without_asset_bytes(tmp_path,monkeypatch):
    cfg=config(tmp_path);data=b"abc";sha=hashlib.sha256(data).hexdigest()
    objects=Objects("Suspended","v2/uploads/test.png",data)
    objects.markers=[{"Key":objects.key,"VersionId":"marker","IsLatest":False}]
    monkeypatch.setattr(repository,"make_client",lambda _:objects)
    result=RemoteAssets(cfg).delete("uploads/test.png",sha,len(data))
    assert not result["present"] and result["delete_markers_retained"]==1 and objects.markers
    assert all(args.get("VersionId")!="marker" for method,args in objects.calls if method=="delete")


def test_fixed_sdk_accepts_conditional_delete_and_absence_verification(tmp_path,monkeypatch):
    cfg=config(tmp_path);client=repository.make_client(cfg);data=b"abc";sha=hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(repository,"make_client",lambda _:client)
    params={"Bucket":cfg.v2_s3_bucket};obj={**params,"Key":"v2/uploads/test.png"}
    with Stubber(client) as stub:
        stub.add_response("get_bucket_versioning",{},params)
        stub.add_response("get_object",{"Body":StreamingBody(io.BytesIO(data),len(data)),"ContentLength":len(data),"ETag":'"abc"'},obj)
        stub.add_response("get_bucket_versioning",{},params)
        stub.add_response("delete_object",{},{**obj,"IfMatch":'"abc"'})
        stub.add_response("get_bucket_versioning",{},params)
        stub.add_client_error("get_object",service_error_code="NoSuchKey",http_status_code=404,expected_params=obj)
        assert not RemoteAssets(cfg).delete("uploads/test.png",sha,len(data))["present"]
        stub.assert_no_pending_responses()
    client.close()


async def test_original_remote_upload_grace_cannot_be_shortened(factory,tmp_path,monkeypatch):
    owner,_,_,asset,root,cfg,now=await upload_prepared(factory,tmp_path)
    objects=bind(cfg,tmp_path,monkeypatch,asset,root)
    async with factory.begin() as db:await set_control(db,"global",True)
    await apply_upload(factory,root,cfg,owner,now,await inspect_upload(factory,root,cfg,now))
    cfg.v2_expired_upload_grace_days=1
    report=await inspect_upload(factory,root,cfg,now+timedelta(days=2),"purge")
    assert not report["candidates"] and report["retained"][0]["reason"]=="original_grace_period"
    assert objects.versions
