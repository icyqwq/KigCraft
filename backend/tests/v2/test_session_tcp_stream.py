"""Actual loopback HTTP/SSE transport with the production app and isolated PostgreSQL."""
import asyncio
import json
import socket
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
import uvicorn

from app.auth.sessions import _hash_token
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession, User
from app.design.models import Design
from app.design.service import event
from app.main import create_app
from .test_api import api
from .test_migrations import migration_database
from .test_account_sessions import seed


@pytest_asyncio.fixture
async def tcp_api(api):
    original, factory, owner, design, branch = api
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port,
        lifespan="on", access_log=False, log_level="warning", timeout_graceful_shutdown=3))
    task = asyncio.create_task(server.serve(sockets=[listener]))

    async def started():
        while not server.started:
            if task.done():
                await task
                raise AssertionError("Temporary server exited before startup")
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(started(), 10)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                    timeout=6, trust_env=False) as client:
            client.cookies.set(get_settings().session_cookie_name,
                               original.cookies.get(get_settings().session_cookie_name))
            assert (await client.get("/health")).status_code == 200
            yield client, factory, owner, design, branch
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 6)
        finally:
            listener.close()


async def frame(lines):
    result = {}
    async for line in lines:
        if not line:
            if result:
                return result
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        result[name] = value.lstrip()
    raise EOFError("SSE stream closed")


async def emit(factory, design, text):
    async with factory.begin() as db:
        await event(db, design, "message.delta", {"text": text})


@pytest.mark.parametrize("reason", ["logout", "expired", "banned", "revoke_others"])
async def test_tcp_stream_closes_after_session_revocation(tcp_api, reason, tmp_path):
    client, factory, owner, design, _ = tcp_api
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
        assert response.headers["x-accel-buffering"] == "no"
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
        "reason": reason, "transport": "uvicorn-loopback-http", "lifespan": "on",
        "old_stream_closed": True, "closure_seconds": closure_seconds,
        "old_cookie_reconnect": rejected.status_code,
        "replacement_session_can_resume": reason != "banned", "new_model_turns": 0,
    }, indent=2), encoding="utf-8")


async def test_tcp_reconnect_replays_after_last_id_and_detects_future_cursor(tcp_api):
    client, factory, _, design, _ = tcp_api
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
