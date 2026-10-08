import hashlib,io
from pathlib import Path
import pytest
from PIL import Image
from app.assets.storage import normalize_upload
from app.core.config import Settings
from app.design.models import Asset,Operation,Wallet
from app.design.service import ApplicationError
from app.operations.worker import LocalWorker
from .test_migrations import migration_database
from .test_operations import factory,setup,request,quoted,submit_request
from .test_worker import ScriptedRuntime

def png(alpha):
    stream=io.BytesIO()
    Image.new("RGBA",(16,16),(255,255,255,alpha)).save(stream,format="PNG")
    return stream.getvalue()

def test_transparent_upload_rejected_but_opaque_white_allowed():
    with pytest.raises(ApplicationError,match="empty_image"):
        normalize_upload(png(0))
    assert normalize_upload(png(255))[1:]==(16,16)

async def test_preexisting_transparent_reference_stops_before_model_and_releases_quota(factory,tmp_path):
    owner,design,branch=await setup(factory)
    storage=tmp_path/"assets";storage.mkdir()
    data=png(0);(storage/"legacy.png").write_bytes(data)
    async with factory.begin() as db:
        asset=Asset(owner_id=owner,object_key="legacy.png",sha256=hashlib.sha256(data).hexdigest(),
            mime_type="image/png",kind="reference",width=16,height=16,byte_size=len(data),status="ready")
        db.add(asset);await db.flush();asset_id=asset.id
    payload=request(attachments=[{"asset_id":asset_id,"usage":"Legacy reference"}])
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch,payload))
    settings=Settings(_env_file=None,app_env="test",v2_enabled=True,v2_new_operations=True,
        v2_runtime_mode="local",v2_local_user_id=owner,v2_runtime_dir=str(tmp_path/"runtime"),v2_storage_dir=str(storage))
    runtime=ScriptedRuntime()
    await LocalWorker(factory,settings,runtime=runtime).run_once()
    assert runtime.calls==[]
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="failed" and op.error_code=="empty_image"
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,0)


def test_transparent_native_output_cannot_be_staged(tmp_path):
    import base64
    from app.codex_runtime.artifacts import stage_image,ArtifactError
    with pytest.raises(ArtifactError,match="empty_image"):
        stage_image({"id":"empty","type":"imageGeneration","status":"completed","result":base64.b64encode(png(0)).decode()},
            runtime_id="runtime",thread_id="thread",turn_id="turn",output_root=tmp_path,staging_root=tmp_path/"staging")


async def test_worker_consumes_terminal_after_rejecting_transparent_output(factory,tmp_path,monkeypatch):
    import base64
    import tests.v2.test_worker as scripted
    worker,runtime,owner,branch,opid=await scripted.prepared(factory,tmp_path)
    monkeypatch.setattr(scripted,"png",lambda:base64.b64encode(png(0)).decode())
    await worker.run_once()
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.state=="failed" and op.upstream_status=="completed"
        assert op.error_code=="image_generation_failed"
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,0)
