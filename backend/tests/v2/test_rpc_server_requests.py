import asyncio
import json
import sys
from pathlib import Path
import pytest
from app.codex_runtime.rpc import CodexRpc, RpcError

FAKE = Path(__file__).parent / "fixtures/rpc_lifecycle_server.py"

def connection(tmp_path, **options):
    return CodexRpc([sys.executable, "-u", str(FAKE)], cwd=tmp_path, timeout=3, **options)

def request(identity="server-1", **params):
    return {"id": identity, "method": "test/request", "params": params}

async def emit(rpc, *frames):
    return await rpc.request("emit", {"frames": frames}, timeout=1)

async def reply(rpc):
    event = await rpc.next_event(timeout=1)
    assert event["method"] == "server/reply"
    return event["params"]["frame"]

async def test_pending_duplicates_invoke_once_and_do_not_stall_rpc(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(method, params):
        calls.append((method, dict(params)))
        entered.set()
        await release.wait()
        return {"decision": "decline"}
    async with connection(tmp_path, request_handler=handler) as rpc:
        frame = request(a=1, b=2)
        await emit(rpc, *([frame] * 20))
        await entered.wait()
        assert await rpc.request("echo", {}) == {"ok": True}
        assert calls == [("test/request", {"a": 1, "b": 2})]
        release.set()
        assert await reply(rpc) == {"id": "server-1", "result": {"decision": "decline"}}
        # An ordered peer barrier proves there is no second handler response queued.
        await emit(rpc, {"method": "barrier", "params": {}})
        assert (await rpc.next_event(timeout=1))["method"] == "barrier"

async def test_completed_duplicate_replays_frozen_reply_with_canonical_parameters(tmp_path):
    calls = []
    returned = {"nested": {"value": "original"}}
    async def handler(method, params):
        calls.append(dict(params))
        params["a"] = "handler mutation"
        return returned
    async with connection(tmp_path, request_handler=handler) as rpc:
        await emit(rpc, request(a=1, b=2))
        first = await reply(rpc)
        returned["nested"]["value"] = "later mutation"
        await emit(rpc, request(b=2, a=1))
        assert await reply(rpc) == first
        assert first["result"] == {"nested": {"value": "original"}}
        assert calls == [{"a": 1, "b": 2}]

@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("change", ["method", "params"])
async def test_request_identity_conflict_closes_connection(tmp_path, completed, change):
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(method, params):
        calls.append(method)
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {}
    async with connection(tmp_path, request_handler=handler) as rpc:
        frame = request(value=1)
        await emit(rpc, frame)
        await entered.wait()
        if completed:
            release.set()
            await reply(rpc)
        conflict = {**frame, change: "other/request" if change == "method" else {"value": 2}}
        with pytest.raises(RpcError, match="server_request_identity_conflict"):
            await emit(rpc, conflict)
        if not completed:
            await asyncio.wait_for(cancelled.wait(), 1)
        assert calls == ["test/request"]
        with pytest.raises(RpcError, match="server_request_identity_conflict"):
            await rpc.next_event(timeout=1)

async def test_string_and_integer_request_ids_are_distinct(tmp_path):
    calls = []
    async def handler(method, params):
        calls.append(params)
        return {}
    async with connection(tmp_path, request_handler=handler) as rpc:
        await emit(rpc, request(1), request("1"))
        responses = [await reply(rpc), await reply(rpc)]
        assert {type(item["id"]) for item in responses} == {int, str}
        assert len(calls) == 2

@pytest.mark.parametrize("bad", [None, {"value": float("nan")}, {"value": object()}])
async def test_invalid_handler_result_is_opaque_and_cached(tmp_path, bad):
    calls = []
    async def handler(method, params):
        calls.append(method)
        return bad
    async with connection(tmp_path, request_handler=handler) as rpc:
        await emit(rpc, request())
        first = await reply(rpc)
        assert first == {"id": "server-1", "error": {"code": -32601, "message": "Request not permitted"}}
        await emit(rpc, request())
        assert await reply(rpc) == first
        assert len(calls) == 1

async def test_handler_timeout_is_cancelled_and_denial_is_replayed(tmp_path):
    cancelled = asyncio.Event()
    calls = []
    async def handler(method, params):
        calls.append(method)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    async with connection(tmp_path, request_handler=handler) as rpc:
        rpc.timeout = .04
        await emit(rpc, request())
        first = await reply(rpc)
        assert cancelled.is_set()
        assert first["error"]["code"] == -32601
        await emit(rpc, request())
        assert await reply(rpc) == first
        assert calls == ["test/request"]

async def test_history_limit_fails_closed_without_evicting_request_ids(tmp_path):
    async with connection(tmp_path, max_server_requests=2) as rpc:
        await emit(rpc, request("one"))
        await reply(rpc)
        await emit(rpc, request("two"))
        await reply(rpc)
        await emit(rpc, request("one"))
        assert (await reply(rpc))["id"] == "one"
        with pytest.raises(RpcError, match="server_request_history_overflow"):
            await emit(rpc, request("three"))
        assert set(rpc._server_requests) == {"one", "two"}

async def test_response_cache_budget_fails_closed(tmp_path):
    async def handler(method, params):
        return {"value": "x" * 50}
    async with connection(tmp_path, request_handler=handler, max_server_response_bytes=130) as rpc:
        await emit(rpc, request("one"))
        await reply(rpc)
        with pytest.raises(RpcError, match="server_response_history_overflow"):
            await emit(rpc, request("two"))
            await reply(rpc)
        assert rpc._server_response_bytes <= 130

async def test_handler_response_still_obeys_frame_limit(tmp_path):
    async def handler(method, params):
        return {"value": "x" * 2000}
    async with connection(tmp_path, request_handler=handler, max_frame_bytes=1024) as rpc:
        with pytest.raises(RpcError, match="outgoing_frame_too_large"):
            await emit(rpc, request())
            await reply(rpc)

async def test_close_cancels_pending_handler_and_clears_connection_history(tmp_path):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def handler(method, params):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    rpc = connection(tmp_path, request_handler=handler)
    await rpc.start()
    await emit(rpc, request())
    await entered.wait()
    await rpc.close()
    assert cancelled.is_set()
    assert not rpc._server_requests and not rpc._server_tasks and not rpc._tasks
    assert rpc._server_response_bytes == 0

async def test_approval_denials_and_unsupported_requests_never_grant_permissions(tmp_path):
    methods = {
        "item/commandExecution/requestApproval": {"decision": "decline"},
        "item/fileChange/requestApproval": {"decision": "decline"},
        "execCommandApproval": {"decision": "denied"},
        "applyPatchApproval": {"decision": "denied"},
        "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    }
    async with connection(tmp_path) as rpc:
        for method, expected in methods.items():
            frame = {"id": method, "method": method, "params": {"private": "dummy-private"}}
            await emit(rpc, frame)
            first = await reply(rpc)
            assert first == {"id": method, "result": expected}
            await emit(rpc, frame)
            assert await reply(rpc) == first
        await emit(rpc, request(private="dummy-private"))
        assert "dummy-private" not in json.dumps(await reply(rpc))

async def test_pending_server_request_limit_is_bounded(tmp_path):
    async def handler(method, params):
        await asyncio.Event().wait()
    async with connection(tmp_path, request_handler=handler) as rpc:
        with pytest.raises(RpcError, match="too_many_server_requests"):
            await emit(rpc, *(request(str(i)) for i in range(65)))
        assert len(rpc._server_requests) == 64

@pytest.mark.parametrize("option", ["max_server_requests", "max_server_response_bytes"])
async def test_invalid_server_request_limits_are_rejected(tmp_path, option):
    with pytest.raises(ValueError):
        connection(tmp_path, **{option: 0})

async def test_transport_eof_cancels_pending_server_handler(tmp_path):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def handler(method, params):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    async with connection(tmp_path, request_handler=handler) as rpc:
        await emit(rpc, request())
        await entered.wait()
        with pytest.raises(RpcError, match="connection_closed"):
            await rpc.request("crash", {})
        await asyncio.wait_for(cancelled.wait(), 1)

async def test_new_connection_does_not_reuse_previous_server_reply(tmp_path):
    calls = []
    async def handler(method, params):
        calls.append(method)
        return {"connection": len(calls)}
    for number in [1, 2]:
        async with connection(tmp_path, request_handler=handler) as rpc:
            await emit(rpc, request())
            assert (await reply(rpc))["result"] == {"connection": number}
        assert not rpc._server_requests
    assert calls == ["test/request", "test/request"]
