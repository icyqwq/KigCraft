"""Real Linux Nginx + production ASGI app over TCP; opt-in isolated WSL fixture."""
import asyncio
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
from datetime import timedelta

import httpx
from PIL import Image
import pytest
import pytest_asyncio
from sqlalchemy import select
import uvicorn

from app.auth.sessions import _hash_token
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession, User
from app.design.models import Design
from app.main import create_app
from .test_api import api
from .test_migrations import migration_database
from .test_account_sessions import seed
from .test_session_tcp_stream import frame, emit

ROOT = Path(__file__).resolve().parents[3]


@pytest_asyncio.fixture
async def nginx_api(api, tmp_path):
    binary = os.environ.get("KIG_V2_NGINX_BINARY")
    distribution = os.environ.get("KIG_V2_NGINX_WSL")
    adapter = os.environ.get("KIG_V2_NGINX_HOST")
    if not all([binary, distribution, adapter]):
        pytest.skip("Requires isolated WSL Nginx binary and host adapter address")
    assert Path(binary).is_file()
    assert ipaddress.ip_address(adapter).is_private
    original, factory, owner, design, branch = api
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((adapter, 0))
    backend_port = listener.getsockname()[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        proxy_port = probe.getsockname()[1]
    prefix = tmp_path / "nginx"
    prefix.mkdir()
    for name in ["logs", "body", "proxy"]:
        (prefix / name).mkdir()
    def linux(path):
        return subprocess.check_output(["wsl.exe", "-d", distribution, "--exec", "wslpath", "-u",
                                        str(Path(path).resolve())], text=True).strip()
    source = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    config = source
    replacements = {"listen 80;": f"listen 127.0.0.1:{proxy_port};",
                    "http://api:8000": f"http://{adapter}:{backend_port}",
                    "/usr/share/nginx/html": linux(ROOT / "frontend/dist")}
    for old, new in replacements.items():
        assert config.count(old) == 1
        config = config.replace(old, new)
    linux_prefix = linux(prefix)
    mime = linux(Path(binary).parents[1] / "conf/mime.types")
    config = (f'daemon off;\nmaster_process off;\npid "{linux_prefix}/nginx.pid";\n'
              f'error_log "{linux_prefix}/logs/error.log" info;\nevents {{ worker_connections 128; }}\nhttp {{\n'
              f'include "{mime}";\naccess_log off;\nclient_body_temp_path "{linux_prefix}/body";\n'
              f'proxy_temp_path "{linux_prefix}/proxy";\n' + config + "\n}\n")
    config_path = prefix / "nginx.conf"
    config_path.write_text(config, encoding="utf-8")
    command = ["wsl.exe", "-d", distribution, "--exec", linux(binary), "-p", linux_prefix + "/",
               "-c", linux(config_path)]
    server = uvicorn.Server(uvicorn.Config(create_app(), lifespan="on", access_log=False,
                                           log_level="warning", timeout_graceful_shutdown=3))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    log = (prefix / "process.log").open("wb")
    try:
        async def backend_started():
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("Backend stopped before startup")
                await asyncio.sleep(0.01)
        await asyncio.wait_for(backend_started(), 10)
        check = await asyncio.create_subprocess_exec(*command, "-t", stdout=asyncio.subprocess.PIPE,
                                                      stderr=asyncio.subprocess.STDOUT)
        output, _ = await check.communicate()
        (prefix / "config-check.log").write_bytes(output)
        assert check.returncode == 0, output.decode(errors="replace")
        process = await asyncio.create_subprocess_exec(*command, stdout=log, stderr=log)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{proxy_port}", timeout=6, trust_env=False) as client:
            client.cookies.set(get_settings().session_cookie_name, original.cookies.get(get_settings().session_cookie_name))
            async def ready():
                while True:
                    if process.returncode is not None:
                        raise AssertionError("Nginx exited: " + (prefix / "process.log").read_text(errors="replace"))
                    try:
                        response = await client.get("/api/auth/me", timeout=1)
                        if response.status_code == 200:
                            assert response.json()["id"] == owner
                            assert response.headers["server"] == "nginx"
                            return
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.05)
            await asyncio.wait_for(ready(), 15)
            (prefix / "fixture.json").write_text(json.dumps({
                "config_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "config_changes": ["loopback listen port", "test backend adapter/port", "workspace static root"],
                "binary_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                "nginx_port": proxy_port, "backend_port": backend_port, "new_model_turns": 0,
            }, indent=2), encoding="utf-8")
            yield client, factory, owner, design, branch
    finally:
        if process and process.returncode is None:
            stop = await asyncio.create_subprocess_exec(*command, "-s", "quit", stdout=log, stderr=log)
            await asyncio.wait_for(stop.wait(), 5)
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                force = await asyncio.create_subprocess_exec(*command, "-s", "stop", stdout=log, stderr=log)
                await asyncio.wait_for(force.wait(), 5)
                await asyncio.wait_for(process.wait(), 5)
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 6)
        finally:
            listener.close()
            log.close()


@pytest.mark.parametrize("reason", ["logout", "expired", "banned", "revoke_others"])
async def test_proxy_stream_closes_after_session_revocation(nginx_api, reason, tmp_path):
    client, factory, owner, design, _ = nginx_api
    cookie = get_settings().session_cookie_name
    token = client.cookies.get(cookie)
    replacement, _ = await seed(factory, owner)
    async with factory() as db:
        cursor = (await db.get(Design, design)).event_sequence
    await emit(factory, design, "before-revocation")
    path = f"/api/v2/designs/{design}/events"
    async with client.stream("GET", path, params={"after": cursor}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["server"] == "nginx"  # Nginx consumes X-Accel-Buffering.
        lines = response.aiter_lines()
        before = await asyncio.wait_for(frame(lines), 4)
        assert before["event"] == "message.delta"
        assert json.loads(before["data"]) == {"text": "before-revocation"}

        if reason == "logout":
            assert (await client.post("/api/auth/logout")).status_code == 200
        elif reason == "revoke_others":
            client.cookies.clear(); client.cookies.set(cookie, replacement)
            assert (await client.post("/api/auth/sessions/revoke-others")).status_code == 200
        else:
            async with factory.begin() as db:
                if reason == "banned":
                    (await db.get(User, owner)).is_banned = True
                else:
                    row = await db.scalar(select(AuthSession).where(AuthSession.session_token_hash == _hash_token(token)))
                    row.expires_at = utc_now() - timedelta(seconds=1)
        await emit(factory, design, "after-revocation-must-not-arrive")
        started = asyncio.get_running_loop().time()
        with pytest.raises(EOFError):
            await asyncio.wait_for(frame(lines), 5)
        closure_seconds = asyncio.get_running_loop().time() - started

    # Browser-style reconnection presents the original cookie and last event ID.
    client.cookies.clear(); client.cookies.set(cookie, token)
    rejected = await client.get(path, headers={"Last-Event-ID": before["id"]})
    assert rejected.status_code == (403 if reason == "banned" else 401)
    client.cookies.clear(); client.cookies.set(cookie, replacement)
    if reason == "banned":
        assert (await client.get(path)).status_code == 403
    else:
        async with client.stream("GET", path, headers={"Last-Event-ID": before["id"]}) as resumed:
            assert resumed.status_code == 200
            next_event = await asyncio.wait_for(frame(resumed.aiter_lines()), 4)
            assert int(next_event["id"]) == int(before["id"]) + 1
            assert json.loads(next_event["data"])["text"] == "after-revocation-must-not-arrive"
    (tmp_path / "transport.json").write_text(json.dumps({
        "reason": reason, "transport": "linux-nginx-production-config-to-uvicorn-tcp", "lifespan": "on",
        "old_stream_closed": True, "closure_seconds": closure_seconds,
        "old_cookie_reconnect": rejected.status_code,
        "replacement_session_can_resume": reason != "banned", "new_model_turns": 0,
    }, indent=2), encoding="utf-8")


async def test_proxy_reconnect_replays_after_last_id_and_detects_future_cursor(nginx_api):
    client, factory, _, design, _ = nginx_api
    async with factory() as db:
        cursor = (await db.get(Design, design)).event_sequence
    await emit(factory, design, "one")
    await emit(factory, design, "two")
    path = f"/api/v2/designs/{design}/events"
    async with client.stream("GET", path, params={"after": cursor}) as response:
        lines = response.aiter_lines()
        one = await asyncio.wait_for(frame(lines), 4)
        assert json.loads(one["data"])["text"] == "one"
    async with client.stream("GET", path, params={"after": cursor},
                             headers={"Last-Event-ID": one["id"]}) as response:
        two = await asyncio.wait_for(frame(response.aiter_lines()), 4)
        assert json.loads(two["data"])["text"] == "two"
        assert int(two["id"]) == int(one["id"]) + 1
    async with client.stream("GET", path, params={"after": int(two["id"]) + 1}) as response:
        assert (await asyncio.wait_for(frame(response.aiter_lines()), 4)) == {
            "event": "resync_required", "data": "{}"}
    assert (await client.get(path, headers={"Last-Event-ID": "invalid"})).status_code == 400


async def test_proxy_flushes_live_events_and_keeps_private_images_private(nginx_api, tmp_path):
    client, factory, _, design, _ = nginx_api
    home = await client.get("/v2/design")
    assert home.status_code == 200 and '<div id="root">' in home.text
    assert home.headers["cross-origin-opener-policy"] == "same-origin"
    assert home.headers["cross-origin-embedder-policy"] == "require-corp"
    assert (await client.get("/hidden.map")).status_code == 404
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "coral").save(image, format="PNG")
    uploaded = await client.post("/api/v2/assets/uploads", files={"file": ("private.png", image.getvalue(), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    asset_path = uploaded.json()["image_url"]
    asset = await client.get(asset_path)
    assert asset.status_code == 200 and asset.content == image.getvalue()
    assert asset.headers["cache-control"] == "private, no-store"
    async with factory() as db:
        cursor = (await db.get(Design, design)).event_sequence
    durations = []
    async with client.stream("GET", f"/api/v2/designs/{design}/events", params={"after": cursor}) as response:
        assert response.status_code == 200
        lines = response.aiter_lines()
        for index in range(3):
            started = asyncio.get_running_loop().time()
            await emit(factory, design, f"live-{index}")
            received = await asyncio.wait_for(frame(lines), 4)
            durations.append(asyncio.get_running_loop().time() - started)
            assert json.loads(received["data"]) == {"text": f"live-{index}"}
            assert int(received["id"]) == cursor + index + 1
    client.cookies.clear()
    assert (await client.get(asset_path)).status_code == 401
    (tmp_path / "incremental.json").write_text(json.dumps({
        "live_event_seconds": durations, "before_next_event_produced": True,
        "private_asset_cache_control": "private, no-store", "anonymous_asset_status": 401,
    }, indent=2), encoding="utf-8")


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("reason", ["logout", "expired", "banned", "revoke_others"])
async def test_proxy_native_browser_hides_private_ui_and_restores_local_draft(nginx_api, tmp_path, width, reason):
    node = os.environ.get("KIG_V2_BROWSER_NODE")
    if not node:
        pytest.skip("Requires Node with the repository Playwright dependencies")
    client, factory, owner, design, _ = nginx_api
    cookie = get_settings().session_cookie_name
    token = client.cookies.get(cookie)
    error_log = (tmp_path / "browser-stderr.log").open("wb")
    process = await asyncio.create_subprocess_exec(node, str(ROOT / "e2e/scripts/nginx-session-ui.mjs"),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=error_log)
    async def send(value):
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()
    async def stage(expected):
        line = await asyncio.wait_for(process.stdout.readline(), 40)
        assert line, "Browser stopped without a phase report"
        value = json.loads(line)
        assert value.get("phase") == expected, value
        return value
    try:
        await send({"base": str(client.base_url).rstrip("/"), "design": design, "cookie": cookie,
            "token": token, "width": width, "reason": reason, "expectedStatus": 403 if reason == "banned" else 401})
        await stage("ready")
        if reason == "logout":
            assert (await client.post("/api/auth/logout")).status_code == 200
        elif reason == "revoke_others":
            replacement, _ = await seed(factory, owner)
            client.cookies.clear()
            client.cookies.set(cookie, replacement)
            assert (await client.post("/api/auth/sessions/revoke-others")).status_code == 200
        else:
            async with factory.begin() as db:
                if reason == "banned":
                    (await db.get(User, owner)).is_banned = True
                else:
                    row = await db.scalar(select(AuthSession).where(AuthSession.session_token_hash == _hash_token(token)))
                    row.expires_at = utc_now() - timedelta(seconds=1)
        await send({"revoked": True})
        await stage("revoked")
        async with factory.begin() as db:
            (await db.get(User, owner)).is_banned = False
        replacement, _ = await seed(factory, owner)
        await send({"token": replacement})
        result = await stage("complete")
        await asyncio.wait_for(process.wait(), 10)
        assert process.returncode == 0
        (tmp_path / "browser.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    finally:
        process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        error_log.close()
