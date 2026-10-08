"""Real isolated PostgreSQL with scripted account sandboxes, not real Docker/model E2E."""
from contextlib import asynccontextmanager
from pathlib import Path
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.codex_runtime.selection import runtime_allowed
from app.core.config import Settings
from app.design.models import Operation, Wallet
from app.design.router import require_new_operation, capabilities
from app.design.service import ApplicationError
from app.operations.worker import LocalWorker
from .test_worker import ScriptedRuntime
from .test_operations import factory, setup, quoted, submit_request
from .test_migrations import migration_database


class Sandbox(ScriptedRuntime):
    def __init__(self, owner, root):
        super().__init__()
        self.owner=owner
        self.work_root=root/owner/"work"
        self.work_root.mkdir(parents=True)
        self.held=False
        self.prepared=[]
    @asynccontextmanager
    async def operation(self):
        assert not self.held
        self.held=True
        try:
            yield
        finally:
            self.held=False
    def workspace(self, operation):
        assert self.held
        result=self.work_root/operation
        result.mkdir()
        self.prepared.append(operation)
        return result
    def input_workspace(self, operation):
        assert self.held
        return self.work_root.parent/"inputs"/operation
    def connection(self, workspace, *, images):
        assert self.held and workspace.is_relative_to(self.work_root)
        return super().connection(workspace,images=images)
    async def identity(self, rpc):
        return hashlib.sha256(self.owner.encode()).hexdigest()


def configuration(owner, users, tmp_path):
    return Settings(_env_file=None,v2_enabled=True,v2_new_operations=True,app_env="production",
        v2_runtime_mode="container",v2_worker_user_id=owner,v2_container_user_ids=users,
        v2_container_image="runtime@sha256:"+"a"*64,v2_egress_image="egress@sha256:"+"b"*64,
        v2_egress_hosts=["model.example.com"],v2_storage_dir=str(tmp_path/"assets"))


async def test_production_workers_claim_only_their_account_and_publish_once(factory,tmp_path):
    first,_,a=await setup(factory)
    second,_,b=await setup(factory)
    one=await submit_request(factory,first,a,await quoted(factory,first,a))
    two=await submit_request(factory,second,b,await quoted(factory,second,b))
    runtime_a,runtime_b=Sandbox(first,tmp_path),Sandbox(second,tmp_path)
    worker_a=LocalWorker(factory,configuration(first,[first,second],tmp_path),runtime=runtime_a)
    worker_b=LocalWorker(factory,configuration(second,[first,second],tmp_path),runtime=runtime_b)
    assert await worker_a.run_once()==one
    assert await worker_a.run_once() is None
    async with factory() as db:
        assert (await db.get(Operation,one)).state=="succeeded"
        assert (await db.get(Operation,two)).state=="queued"
        assert (await db.get(Wallet,first)).balance==4
        assert (await db.get(Wallet,second)).balance==5
    assert await worker_b.run_once()==two
    assert runtime_a.prepared==[one] and runtime_b.prepared==[two]
    assert worker_a.root!=worker_b.root and not runtime_a.held and not runtime_b.held
    assert (runtime_a.work_root.parent/"inputs"/one/"inputs").is_dir()
    assert not (runtime_a.work_root/one/"inputs").exists()
    with pytest.raises(ApplicationError,match="not_found"):
        await worker_a.retry_publish(two)
    async with factory() as db:
        assert (await db.get(Wallet,second)).balance==4


async def test_container_capabilities_and_submission_admission_share_configuration(tmp_path,monkeypatch):
    import app.design.router as router
    owner,other=str(uuid4()),str(uuid4())
    cfg=configuration(owner,[owner],tmp_path)
    monkeypatch.setattr(router,"get_settings",lambda:cfg)
    require_new_operation(SimpleNamespace(id=owner))
    assert (await capabilities()).generation is True
    with pytest.raises(ApplicationError,match="runtime_not_configured"):
        require_new_operation(SimpleNamespace(id=other))
    cfg.v2_container_image="runtime:latest"
    assert (await capabilities()).generation is False
    with pytest.raises(ApplicationError,match="runtime_not_configured"):
        require_new_operation(SimpleNamespace(id=owner))
    cfg.v2_new_operations=False
    with pytest.raises(ApplicationError,match="new_operations_disabled"):
        require_new_operation(SimpleNamespace(id=owner))


def test_worker_without_explicit_owner_cannot_claim_all_accounts(tmp_path):
    cfg=configuration("",[str(uuid4())],tmp_path)
    with pytest.raises(ApplicationError,match="runtime_not_configured"):
        LocalWorker(None,cfg,runtime=object())


async def test_worker_cannot_be_rebound_by_mutating_account_configuration(factory,tmp_path):
    owner,_,branch=await setup(factory)
    other,_,other_branch=await setup(factory)
    operation=await submit_request(factory,other,other_branch,await quoted(factory,other,other_branch))
    sandbox=Sandbox(owner,tmp_path)
    worker=LocalWorker(factory,configuration(owner,[owner,other],tmp_path),runtime=sandbox)
    worker.settings.v2_worker_user_id=other
    assert await worker.run_once() is None
    with pytest.raises(ApplicationError,match="runtime_not_configured"):
        await worker.reconcile(operation)
    assert sandbox.prepared==[]


async def test_container_recovery_uses_bound_owner_without_local_account(factory,tmp_path):
    from .test_reconciliation import unknown
    worker,runtime,owner,branch,operation,response,calls=await unknown(factory,tmp_path)
    worker.settings.v2_runtime_mode="container"
    worker.settings.v2_worker_user_id=owner
    worker.settings.v2_container_user_ids=[owner]
    worker.settings.v2_local_user_id=""
    assert await worker.reconcile(operation)=="succeeded"
    assert calls==["thread/read"]
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(4,0)


async def test_uploaded_reference_uses_same_read_only_input_path_for_review_and_generation(factory,tmp_path):
    import base64
    from app.assets.storage import upload_asset
    from .test_worker import png
    from .test_operations import request
    owner,_,branch=await setup(factory)
    cfg=configuration(owner,[owner],tmp_path)
    asset=upload_asset(base64.b64decode(png()),owner,tmp_path/"assets")
    async with factory.begin() as db:
        db.add(asset)
    payload=request(attachments=[{"asset_id":asset.id,"usage":"wig fibers"}])
    operation=await submit_request(factory,owner,branch,await quoted(factory,owner,branch,payload))
    sandbox=Sandbox(owner,tmp_path)
    worker=LocalWorker(factory,cfg,runtime=sandbox)
    assert await worker.run_once()==operation
    paths=[]
    for images,method,params in sandbox.calls:
        if method=="turn/start":
            # The application's own style references ride with the main turn only and are not the user's input.
            paths.extend(i["path"] for i in params["input"] if i["type"]=="localImage" and "style" not in Path(i["path"]).parts)
    assert len(paths)==2 and paths[0]==paths[1]
    source=sandbox.input_workspace(operation) if sandbox.held else sandbox.work_root.parent/"inputs"/operation
    expected=source/"inputs"/("001-"+asset.sha256+(".png" if asset.mime_type=="image/png" else ".jpg"))
    assert paths[0]==str(expected.resolve())
    assert hashlib.sha256(expected.read_bytes()).hexdigest()==asset.sha256
    assert not expected.is_relative_to(sandbox.work_root)
    async with factory() as db:
        assert (await db.get(Operation,operation)).state=="succeeded"
