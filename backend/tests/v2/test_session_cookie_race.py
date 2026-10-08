"""Real Chromium Set-Cookie ordering against isolated production authentication routes."""
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


class HoldLogoutResponse:
    """Only delays response headers; production route/database/cookies stay intact."""
    def __init__(self, app):
        self.app = app
        self.held = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/__qa/"):
            if scope["path"] == "/__qa/release":
                self.release.set()
            body = json.dumps({"held": self.held.is_set()}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        async def delayed(message):
            if (scope["type"] == "http" and scope["path"] == "/api/auth/logout"
                    and (b"x-qa-hold", b"logout") in scope["headers"]
                    and message["type"] == "http.response.start"):
                self.held.set()
                await asyncio.wait_for(self.release.wait(), 20)
            await send(message)

        await self.app(scope, receive, delayed)


@pytest.mark.parametrize("other_tab", [False, True], ids=["same-tab", "two-tabs"])
@pytest.mark.parametrize("same_owner", [False, True], ids=["switch-account", "same-account"])
async def test_delayed_logout_cannot_clear_new_browser_login(api, monkeypatch, tmp_path, other_tab, same_owner):
    node = os.environ.get("KIG_V2_BROWSER_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Set KIG_V2_BROWSER_NODE to Node with e2e Playwright installed")
    _, factory, owner, _, _ = api
    other = owner if same_owner else (await setup(factory))[0]
    password = "Synthetic-cookie-race-only-2026"
    password_hash = hash_password(password)
    async with factory.begin() as db:
        first = await db.get(User, owner)
        second = await db.get(User, other)
        first.password_hash = second.password_hash = password_hash
        first_email, second_email = first.email, second.email
    monkeypatch.setenv("CAPTCHA_ENABLED", "false")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    gate = HoldLogoutResponse(create_app())
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
        script = Path(__file__).resolve().parents[3] / "e2e/scripts/session-cookie-race.mjs"
        process = await asyncio.create_subprocess_exec(node, str(script),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        payload = json.dumps({"base": f"http://127.0.0.1:{port}", "otherTab": other_tab,
            "firstEmail": first_email, "secondEmail": second_email, "secondOwner": other,
            "password": password, "cookie": get_settings().session_cookie_name}).encode()
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), 35)
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
        report = json.loads(stdout)
        assert report["newCookieSurvivedOldLogout"] is True
        (tmp_path / "cookie-race.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
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
