import json
import os
import subprocess
import sys
from uuid import uuid4
from datetime import timedelta

import pytest
from pathlib import Path
import io
from PIL import Image
from app.auth.models import UserRole
from app.auth.sessions import _hash_token
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession, GenerationJob, GenerationEvent, GenerationOutput, Project, User, JobStatus
from .test_api import api
from .test_migrations import migration_database, migrate, ROOT
from .test_legacy_inventory import fingerprints

BLOCKED = [
    "app.generation.provider", "app.generation.queue", "app.generation.router",
    "app.generation.job_repository", "app.generation.job_store", "app.generation.presets",
    "app.generation.codex_app_server", "app.generation.codex_usage",
    "app.generation.detail_analysis", "app.generation.local_edit",
    "app.admin.router", "app.audit.router", "app.codex_profiles.router",
    "app.codex_profiles.session_cleanup",
]

async def test_v2_api_and_worker_import_without_legacy_execution_modules_or_keys(tmp_path, migration_database):
    assert migrate(migration_database).returncode == 0
    code = """
import importlib.abc,json,sys
blocked=set(json.loads(sys.argv[1]))
class RefuseLegacy(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname in blocked:raise RuntimeError("Forbidden legacy import: "+fullname)
sys.meta_path.insert(0,RefuseLegacy())
from app.main import create_app
import app.operations.worker
app=create_app()
schema=app.openapi()
assert "/api/v2/branches/{branch_id}/turns" in schema["paths"]
assert "/api/generation/jobs" in schema["paths"]
assert "post" not in schema["paths"]["/api/generation/jobs"]
assert "/api/admin/provider-presets" not in schema["paths"]
assert "/api/admin/codex-profiles" not in schema["paths"]
assert not blocked.intersection(sys.modules)
import asyncio
from httpx import ASGITransport, AsyncClient
from app.db.session import get_engine
async def verify_startup():
 async with app.router.lifespan_context(app):
  async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
   assert (await client.get("/health")).status_code==200
   assert (await client.get("/api/generation/jobs")).status_code==401
   assert (await client.post("/api/generation/jobs",content=b"invalid")).status_code==503
 await get_engine().dispose()
asyncio.run(verify_startup())
assert not blocked.intersection(sys.modules)
print(json.dumps({"state":"passed","blocked_modules":len(blocked)}))
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("QWEN", "SEEDREAM", "DASHSCOPE", "ARK_"))}
    env |= {"PYTHONPATH": str(ROOT), "V2_ENABLED": "true", "APP_ENV": "test",
            "DATABASE_URL": migration_database,
            "EMAIL_PROVIDER": "console", "CAPTCHA_ENABLED": "false", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-c", code, json.dumps(BLOCKED)],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "passed"

async def test_v2_rejects_all_legacy_execution_paths_before_payload_or_provider(api):
    client, factory, _, _, _ = api
    before = await fingerprints(factory)
    for prefix in ["", "/api"]:
        for path in ["/generation/jobs","/generation/detail-analysis","/generation/local-revision-jobs",
                     "/generation/projects/old/jobs","/generation/jobs/old/accept","/generation/jobs/old/cancel"]:
            response = await client.post(prefix + path, content=b"not JSON")
            assert response.status_code == 503
            assert response.json()["detail"] == "legacy_execution_disabled"
        assert (await client.get(prefix+"/generation/queue-status")).status_code == 503
        for path in ["/admin/provider-presets","/admin/codex-profiles","/audit/summary"]:
            assert (await client.get(prefix+path)).status_code == 404
        assert (await client.put(prefix+"/admin/provider-presets/active",json={"preset_id":"qwen_seedream"})).status_code == 404
    assert await fingerprints(factory) == before

async def test_legacy_history_does_not_initialize_fail_refund_or_rewrite_jobs(api, monkeypatch, tmp_path):
    client, factory, owner, _, _ = api
    monkeypatch.setattr(get_settings(), "codex_output_dir", str(tmp_path / "generated"))
    png = io.BytesIO()
    Image.new("RGB", (19, 13), "coral").save(png, format="PNG")
    async with factory.begin() as db:
        p=Project(user_id=owner,name="Original legacy project"); db.add(p); await db.flush()
        other=User(email=str(uuid4())+"@example.com");db.add(other);await db.flush()
        jobs=[]
        for provider,state in [("qwen_seedream","running"),("seedream","accepted"),("codex","")]:
            job=GenerationJob(project_id=p.id,user_id=owner,provider=provider,runtime_status=state,status=JobStatus.running,
                character_session_id="",generation_mode="front_design",phase_label="Original phase",
                prompt_payload={"character_session_id":"original-thread","private":"must not appear"},
                accepted_output_index=1 if state=="accepted" else None)
            db.add(job);await db.flush();jobs.append(job)
            db.add(GenerationOutput(job_id=job.id,index=1,object_key="legacy/"+job.id+".png",
                image_url="/api/generated/legacy/"+job.id+".png",width=19,height=13))
            db.add(GenerationEvent(job_id=job.id,sequence=1,type="original",progress=0,message="Stored legacy event",payload={"provider":provider}))
    for job in jobs:
        path = Path(get_settings().codex_output_dir) / "legacy" / (job.id + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png.getvalue())
    before=await fingerprints(factory)
    for prefix in ["", "/api"]:
        page=(await client.get(prefix+"/generation/jobs?limit=1")).json()
        assert page["total"]==3 and len(page["jobs"])==1
        page2=(await client.get(prefix+"/generation/jobs?limit=1&offset=1")).json()
        assert page2["jobs"][0]["id"]!=page["jobs"][0]["id"]
        filtered=(await client.get(prefix+"/generation/jobs?status=accepted&generation_mode=front_design")).json()
        assert [x["id"] for x in filtered["jobs"]]==[jobs[1].id]
        for job,expected in zip(jobs,["running","accepted","generating"]):
            response=await client.get(prefix+"/generation/jobs/"+job.id)
            assert response.status_code==200 and response.headers["cache-control"]=="private, no-store"
            item=response.json()
            assert item["provider"]==job.provider and item["status"]==expected and item["character_session_id"]=="original-thread"
            assert item["accepted_output_index"]==job.accepted_output_index
            assert item["outputs"][0]["image_url"]=="/api/generated/legacy/"+job.id+".png"
            assert "must not appear" not in response.text
            image_response = await client.get(item["outputs"][0]["image_url"])
            assert image_response.status_code == 200 and image_response.content == png.getvalue()
            assert (await client.head(item["outputs"][0]["image_url"])).status_code == 200
            events=await client.get(prefix+"/generation/jobs/"+job.id+"/events")
            assert events.status_code==200 and events.headers["cache-control"]=="private, no-store"
            assert len(events.json())==1 and events.json()[0]["message"]=="Stored legacy event"
    assert await fingerprints(factory)==before
    token="history-other-"+uuid4().hex
    async with factory.begin() as db:
        db.add(AuthSession(user_id=other.id,session_token_hash=_hash_token(token),expires_at=utc_now()+timedelta(hours=1),created_ip="127.0.0.1"))
    client.cookies.clear();client.cookies.set(get_settings().session_cookie_name,token)
    before=await fingerprints(factory)
    assert (await client.get("/api/generation/jobs")).json()["jobs"]==[]
    for prefix in ["","/api"]:
        for suffix in ["","/events"]:
            foreign=await client.get(prefix+"/generation/jobs/"+jobs[0].id+suffix)
            absent=await client.get(prefix+"/generation/jobs/"+str(uuid4())+suffix)
            assert foreign.status_code==absent.status_code==404
            assert foreign.content==absent.content
    url = "/api/generated/legacy/" + jobs[0].id + ".png"
    for method in ["GET","HEAD"]:
        foreign=await client.request(method,url)
        absent=await client.request(method,"/api/generated/legacy/"+str(uuid4())+".png")
        assert foreign.status_code==absent.status_code==404
        assert foreign.content==absent.content
    client.cookies.clear()
    assert (await client.get(url)).status_code == 404
    assert (await client.get("/api/generation/jobs")).status_code==401
    after_denials = await fingerprints(factory)
    # Private legacy denials now increment telemetry, but never business state.
    assert {k:v for k,v in after_denials.items() if k != "audit_counters"} == {
        k:v for k,v in before.items() if k != "audit_counters"}

    async with factory.begin() as db:
        user = await db.get(User, other.id)
        user.role = UserRole.admin
    client.cookies.set(get_settings().session_cookie_name, token)
    before = await fingerprints(factory)
    assert (await client.get("/api/generation/jobs/" + jobs[0].id)).status_code == 200
    assert (await client.get("/api/generation/jobs/" + jobs[0].id + "/events")).status_code == 200
    assert (await client.get(url)).content == png.getvalue()
    assert await fingerprints(factory) == before
