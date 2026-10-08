"""Paused deployment uses only compatible APIs; no worker or runtime credentials."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from httpx import ASGITransport, AsyncClient
from app.core.config import get_settings
from app.design.models import Asset, Operation, Wallet
from app.main import create_app
from .test_api import api
from .test_migrations import migration_database
from .test_manual import base
from .test_operations import quoted, submit_request, request
from .test_account_sessions import seed
from .test_legacy_inventory import fingerprints

COMPOSE=Path(__file__).resolve().parents[3]/"deploy/v2/compose.paused.json"

def test_paused_services_have_fixed_images_and_no_legacy_worker_or_runtime_mount():
    services=json.loads(COMPOSE.read_text())["services"]
    assert set(services)=={"api","frontend","migrate"}
    for service in services.values():
        assert "@sha256:" in service["image"]
        assert "build" not in service and service["pull_policy"]=="never"
        assert service["read_only"] and service["security_opt"]==["no-new-privileges:true"]
        assert all("/root" not in v["target"] and "runtime" not in v["target"] for v in service.get("volumes",[]))
    assert services["api"]["entrypoint"]==["python","-m","uvicorn"]
    assert services["migrate"]["profiles"]==["migration"]
    assert services["migrate"]["entrypoint"]==["python","-m","app.db.migrate"]
    assert "depends_on" not in services["migrate"]
    assert "ports" not in services["api"]
    assert services["frontend"]["ports"][0].startswith("127.0.0.1:")
    assert services["frontend"]["ports"][0].endswith(":8080")
    assert "cap_add" not in services["frontend"]
    source=(COMPOSE.parents[2]/"frontend/nginx.conf").read_text()
    assert (COMPOSE.parent/"nginx.paused-server.conf").read_text()==source.replace("listen 80;","listen 8080;")
    assert all(not v["bind"]["create_host_path"] for v in services["api"]["volumes"])

@pytest.mark.parametrize("missing", [None,"V2_API_SHA256","V2_FRONTEND_SHA256","V2_API_ENV_FILE"])
def test_real_compose_resolves_digest_images_and_rejects_missing_inputs(tmp_path,missing):
    docker=shutil.which("docker")
    if not docker:pytest.skip("Docker CLI required; daemon is not needed")
    # Synthetic interpolation data; never read or print a real private env file.
    envfile=tmp_path/"api.env"
    envfile.write_text("V2_NEW_OPERATIONS=true\nV2_RUNTIME_MODE=local\nCODEX_UPDATE_ENABLED=true\n",encoding="utf-8")
    empty=tmp_path/"empty.env";empty.write_text("",encoding="utf-8")
    env={key:value for key,value in os.environ.items() if not key.startswith(("V2_","COMPOSE_"))}
    env.update(V2_API_REPOSITORY="example.invalid/kig-api",V2_API_SHA256="a"*64,
        V2_FRONTEND_REPOSITORY="example.invalid/kig-web",V2_FRONTEND_SHA256="b"*64,
        V2_API_ENV_FILE=str(envfile),V2_MIGRATION_ENV_FILE=str(empty),V2_API_UID="10001",V2_API_GID="10001",V2_FRONTEND_UID="101",V2_FRONTEND_GID="101",
        V2_ASSETS_DIR=str(tmp_path),V2_LEGACY_GENERATED_DIR=str(tmp_path),V2_LEGACY_REFERENCES_DIR=str(tmp_path))
    if missing:env.pop(missing)
    result=subprocess.run([docker,"compose","--env-file",str(empty),"-f",str(COMPOSE),"config","--format","json"],
        cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    if missing:
        assert result.returncode!=0
        assert missing in result.stderr
        return
    assert result.returncode==0,result.stderr
    services=json.loads(result.stdout)["services"]
    for service in services.values():
        assert re.fullmatch(r"example\.invalid/kig-(api|web)@sha256:[a-f0-9]{64}",service["image"])
    resolved=services["api"]["environment"]
    assert resolved["V2_NEW_OPERATIONS"]=="false"
    assert resolved["V2_RUNTIME_MODE"]=="disabled"
    assert resolved["CODEX_UPDATE_ENABLED"]=="false"

async def test_paused_api_restart_keeps_completed_images_and_cancel_without_new_execution(api,monkeypatch,tmp_path):
    _,factory,_,_,_=api
    root=Path(get_settings().v2_storage_dir)
    owner,branch,revision=await base(factory,root)
    # A second queued request may be cancelled after the pause; it must not dispatch.
    payload=await quoted(factory,owner,branch,request(client_request_id="queued-before-pause",expected_branch_version=1,base_revision_id=revision.id))
    queued=await submit_request(factory,owner,branch,payload)
    token,_=await seed(factory,owner)
    settings=get_settings()
    for key,value in json.loads(COMPOSE.read_text())["services"]["api"]["environment"].items():
        if hasattr(settings,key.lower()):
            old=getattr(settings,key.lower())
            monkeypatch.setattr(settings,key.lower(),value=="true" if isinstance(old,bool) else value)
    monkeypatch.setattr(settings,"v2_storage_dir",str(root))
    async with factory() as db:
        asset=await db.get(Asset,revision.clean_asset_id)
        original=(root/asset.object_key).read_bytes()
        from app.images.delivery import watermarked_jpeg
        # Clients get the watermarked rendition of AI output; the stored object stays untouched.
        delivered=watermarked_jpeg(root/asset.object_key,key="expected-"+asset.id,locale="zh-CN")
        assert delivered!=original
    before=await fingerprints(factory)
    # Exercise actual lifespan/schema check twice, like replacing/restarting a compatible API.
    for _ in range(2):
        app=create_app()
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
                client.cookies.set(settings.session_cookie_name,token)
                assert (await client.get("/health")).status_code==200
                caps=(await client.get("/api/v2/capabilities")).json()
                assert caps["enabled"] and not caps["new_operations"]
                view=await client.get("/api/v2/designs/"+revision.design_id)
                assert view.status_code==200 and view.json()["current_revision"]["id"]==revision.id
                image=await client.get("/api/v2/assets/"+asset.id)
                assert image.status_code==200 and image.content==delivered
                for endpoint in ["quotes","turns"]:
                    body=payload.model_dump(mode="json")
                    if endpoint=="quotes":body.pop("quote_id")
                    refused=await client.post(f"/api/v2/branches/{branch}/{endpoint}",json=body)
                    assert refused.status_code==503 and refused.json()["code"]=="new_operations_disabled"
                assert (await client.post("/api/generation/jobs",content=b"invalid")).status_code==503
    after=await fingerprints(factory)
    assert {k:v for k,v in after.items() if k!="audit_counters"} == {
        k:v for k,v in before.items() if k!="audit_counters"}
    from app.admin.access_collector import PREFIX
    from sqlalchemy import select
    from app.db.models import AuditCounter
    async with factory() as db:
        counters=list(await db.scalars(select(AuditCounter)))
    assert len(counters)==2
    assert all(row.key.startswith(PREFIX) and row.key.endswith(":heartbeat") and row.value==-2 for row in counters)
    app=create_app()
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        client.cookies.set(settings.session_cookie_name,token)
        assert (await client.post(f"/api/v2/operations/{queued}/cancel")).status_code==200
        assert (await client.get("/api/v2/assets/"+asset.id)).content==delivered
        assert (root/asset.object_key).read_bytes()==original
    async with factory() as db:
        completed=await db.get(Operation,revision.operation_id);cancelled=await db.get(Operation,queued)
        wallet=await db.get(Wallet,owner)
        assert completed.state=="succeeded" and completed.settled_units==1
        assert cancelled.state=="cancelled" and cancelled.upstream_thread_id is None
        assert wallet.reserved==0 and wallet.balance==4
