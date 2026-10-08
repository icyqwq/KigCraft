"""Real frontend API bundle + Chromium + production routes, with login headers delayed."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket

import pytest
import uvicorn

from app.auth.passwords import hash_password
from app.core.config import get_settings
from app.db.models import User
from app.main import create_app
from .test_api import api
from .test_migrations import migration_database
from .test_operations import setup


class HoldLoginResponse:
    def __init__(self, app, bundle):
        self.app, self.bundle = app, bundle
        self.active = False
        self.held = asyncio.Event()
        self.release = asyncio.Event()
        self.mutations = self.reads = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path.startswith("/__qa/"):
            if path == "/__qa/hold-login":
                self.active = True
            elif path == "/__qa/release":
                self.release.set()
            body = self.bundle if path == "/__qa/client.js" else json.dumps({
                "held": self.held.is_set(), "mutations": self.mutations, "reads": self.reads}).encode()
            content_type = b"text/javascript" if path == "/__qa/client.js" else b"application/json"
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", content_type)]})
            await send({"type": "http.response.body", "body": body})
            return
        hold = False
        if self.active and path.startswith("/api/auth/"):
            if scope["method"] == "POST":
                self.mutations += 1
                hold = path == "/api/auth/login/password" and self.mutations == 1
            elif path == "/api/auth/me":
                self.reads += 1

        async def delayed(message):
            if hold and message["type"] == "http.response.start":
                self.held.set()
                await asyncio.wait_for(self.release.wait(), 20)
            await send(message)
        await self.app(scope, receive, delayed)


@pytest.mark.parametrize("other_tab", [False, True], ids=["same-tab", "two-tabs"])
@pytest.mark.parametrize("mode", ["login-logout", "login-login", "login-read"])
async def test_browser_orders_session_requests(api, monkeypatch, tmp_path, mode, other_tab):
    node = os.environ.get("KIG_V2_BROWSER_NODE") or shutil.which("node")
    bundle = os.environ.get("KIG_V2_AUTH_CLIENT_BUNDLE")
    if not node or not bundle:
        pytest.skip("Requires Node/Playwright and KIG_V2_AUTH_CLIENT_BUNDLE built from frontend/src/api/client.ts")
    _, factory, owner, _, _ = api
    other = (await setup(factory))[0]
    password = "Synthetic-session-order-only-2026"
    password_hash = hash_password(password)
    async with factory.begin() as db:
        first, second = await db.get(User, owner), await db.get(User, other)
        first.password_hash = second.password_hash = password_hash
        first_email, second_email = first.email, second.email
    monkeypatch.setenv("CAPTCHA_ENABLED", "false")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    gate = HoldLoginResponse(create_app(), Path(bundle).read_bytes())
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(gate, lifespan="on", access_log=False,
                                           log_level="warning", timeout_graceful_shutdown=3))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    try:
        async def started():
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("Server stopped before startup")
                await asyncio.sleep(0.01)
        await asyncio.wait_for(started(), 10)
        script = Path(__file__).resolve().parents[3] / "e2e/scripts/session-request-order.mjs"
        process = await asyncio.create_subprocess_exec(node, str(script),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        payload = json.dumps({"base": f"http://127.0.0.1:{port}", "otherTab": other_tab, "mode": mode,
            "firstEmail": first_email, "secondEmail": second_email, "firstOwner": owner,
            "secondOwner": other, "password": password}).encode()
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), 35)
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
        report = json.loads(stdout)
        assert report["finalIdentityCorrect"] is True
        (tmp_path / "session-order.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        gate.release.set()
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 6)
        finally:
            listener.close()
