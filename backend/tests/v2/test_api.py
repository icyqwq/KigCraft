import io
from datetime import timedelta
from uuid import uuid4
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.auth.sessions import _hash_token
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession
from app.db.session import reset_session_factory, get_engine
from app.main import create_app
from .test_migrations import migration_database, migrate
from .test_operations import setup

@pytest_asyncio.fixture
async def api(migration_database, monkeypatch, tmp_path):
    assert migrate(migration_database).returncode == 0
    monkeypatch.setenv("DATABASE_URL", migration_database)
    monkeypatch.setenv("V2_ENABLED", "true")
    monkeypatch.setenv("V2_NEW_OPERATIONS", "true")
    monkeypatch.setenv("V2_RUNTIME_MODE", "local")
    monkeypatch.setenv("V2_STORAGE_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("CODEX_SESSION_CLEANUP_ENABLED", "false")
    get_settings.cache_clear()
    reset_session_factory()
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    owner, design, branch = await setup(factory)
    monkeypatch.setenv("V2_LOCAL_USER_ID", owner)
    get_settings.cache_clear()
    token = "test-" + uuid4().hex
    async with factory.begin() as db:
        db.add(AuthSession(user_id=owner, session_token_hash=_hash_token(token),
                           expires_at=utc_now() + timedelta(hours=1), created_ip="127.0.0.1"))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(get_settings().session_cookie_name, token)
        yield client, factory, owner, design, branch
    await get_engine().dispose()
    reset_session_factory()
    get_settings.cache_clear()

async def test_api_quotes_and_submits_idempotently_with_existing_login(api):
    client, _, owner, design, branch = api
    payload = {"client_request_id": "api-request-1", "intent": "generate_or_clarify",
               "text": "coral wig", "expected_branch_version": 0}
    price = await client.post(f"/api/v2/branches/{branch}/quotes", json=payload)
    assert price.status_code == 200, price.text
    payload["quote_id"] = price.json()["id"]
    first = await client.post(f"/api/v2/branches/{branch}/turns", json=payload)
    second = await client.post(f"/api/v2/branches/{branch}/turns", json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    snapshot = (await client.get(f"/api/v2/designs/{design}")).json()
    assert len(snapshot["messages"]) == 1
    assert snapshot["snapshot_sequence"] > 0
    assert "upstream_thread_id" not in repr(snapshot)
    assert (await client.get("/api/v2/account/quota")).json()["available"] == 4

async def test_assets_are_private_and_cross_owner_is_indistinguishable(api):
    client, factory, _, _, _ = api
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "coral").save(image, format="PNG")
    upload = await client.post("/api/v2/assets/uploads", files={"file": ("ref.png", image.getvalue(), "image/png")})
    assert upload.status_code == 200, upload.text
    url = upload.json()["image_url"]
    own = await client.get(url)
    assert own.status_code == 200
    assert own.headers["cache-control"] == "private, max-age=604800, immutable"
    owner, _, _ = await setup(factory)
    token = "second-" + uuid4().hex
    async with factory.begin() as db:
        db.add(AuthSession(user_id=owner, session_token_hash=_hash_token(token),
                           expires_at=utc_now() + timedelta(hours=1), created_ip="127.0.0.1"))
    client.cookies.clear()
    client.cookies.set(get_settings().session_cookie_name, token)
    assert (await client.get(url)).status_code == 404
    assert (await client.get("/api/v2/assets/does-not-exist")).status_code == 404

async def test_unauthenticated_v2_access_is_denied(api):
    client, _, _, design, _ = api
    client.cookies.clear()
    assert (await client.get(f"/api/v2/designs/{design}")).status_code == 401

async def test_history_uses_same_http_quote_submit_and_read_only_branch_selection(api,tmp_path):
    from app.operations.worker import LocalWorker
    from app.design.models import Branch, Operation
    from .test_worker import ScriptedRuntime
    client,factory,owner,design,branch=api
    settings=get_settings().model_copy(update={"v2_runtime_dir":str(tmp_path/"runtime")})
    worker=LocalWorker(factory,settings,runtime=ScriptedRuntime())
    payload={"client_request_id":"api-original","intent":"generate_or_clarify","text":"Coral original","expected_branch_version":0}
    price=(await client.post(f"/api/v2/branches/{branch}/quotes",json=payload)).json()
    sent=await client.post(f"/api/v2/branches/{branch}/turns",json={**payload,"quote_id":price["id"]})
    assert sent.status_code==202
    await worker.run_once()
    original=(await client.get(f"/api/v2/designs/{design}")).json()
    image=original["revisions"][0]
    retry={"client_request_id":"api-regenerate","intent":"generate_or_clarify","text":"","expected_branch_version":1,
           "regenerate_revision_id":image["id"]}
    price=await client.post(f"/api/v2/branches/{branch}/quotes",json=retry)
    assert price.status_code==200,price.text
    assert price.json()["request_preview"]["text"]=="Coral original"
    assert price.json()["request_preview"]["base_image_url"] is None
    assert len((await client.get(f"/api/v2/designs/{design}")).json()["branches"])==1
    sent=await client.post(f"/api/v2/branches/{branch}/turns",json={**retry,"quote_id":price.json()["id"]})
    assert sent.status_code==202,sent.text
    child=sent.json()["branch_id"]
    assert child!=branch
    await worker.run_once()
    current=(await client.get(f"/api/v2/designs/{design}")).json()
    assert current["branch"]["id"]==child and len(current["branches"])==2
    old=(await client.get(f"/api/v2/designs/{design}?branch_id={branch}")).json()
    assert old["branch"]["current_revision_id"]==image["id"]
    assert old["snapshot_sequence"]==current["snapshot_sequence"]
    assert (await client.get(f"/api/v2/designs/{design}")).json()["branch"]["id"]==child
    assert (await client.get("/api/v2/account/quota")).json()["balance"]==3
