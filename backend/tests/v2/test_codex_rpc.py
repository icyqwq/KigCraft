import asyncio
import sys
from pathlib import Path
import pytest
from app.codex_runtime.rpc import CodexRpc, RpcClosed, RpcError, RpcTimeout
FAKE = Path(__file__).parent / "fixtures/fake_app_server.py"

@pytest.fixture
def rpc(tmp_path):
    return CodexRpc([sys.executable, "-u", str(FAKE)], cwd=tmp_path, timeout=3)

async def test_interleaved_responses_and_notifications(rpc):
    async with rpc:
        a, b = await asyncio.gather(rpc.request("interleave", {}), rpc.request("interleave", {}))
        assert a["request"] != b["request"]
        assert (await rpc.next_event(timeout=1))["params"]["delta"] == "\u4f60\u597d"

async def test_server_approval_denied_without_stalling_reader(rpc):
    async with rpc:
        result = await rpc.request("approval", {})
        assert result["result"] == {"decision": "decline"}
        assert (await rpc.next_event(timeout=1))["method"] == "during/approval"

async def test_stderr_pressure_does_not_deadlock_or_retain_secrets(rpc):
    async with rpc:
        assert await rpc.request("stderr", {}) == {"ok": True}
        assert rpc.stderr_bytes > 65536
        assert "private-token" not in repr(vars(rpc))

async def test_timeout_late_response_and_next_request(rpc):
    async with rpc:
        with pytest.raises(RpcTimeout, match="outcome_unknown"):
            await rpc.request("late", {}, timeout=0.03)
        assert await rpc.request("echo", {}) == {"ok": True}
        assert rpc.late_responses == 1

async def test_cancelled_request_does_not_cancel_others(rpc):
    async with rpc:
        task = asyncio.create_task(rpc.request("stall", {}))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await rpc.request("echo", {}) == {"ok": True}

@pytest.mark.parametrize("method", ["malformed", "invalid-utf8", "crash"])
async def test_bad_stream_fails_pending_and_event_waiters(rpc, method):
    async with rpc:
        waiter = asyncio.create_task(rpc.next_event())
        with pytest.raises(RpcError):
            await rpc.request(method, {})
        with pytest.raises(RpcError):
            await waiter

async def test_large_frame_fails_closed(tmp_path):
    async with CodexRpc([sys.executable, "-u", str(FAKE)], cwd=tmp_path, max_frame_bytes=1024) as rpc:
        with pytest.raises(RpcError):
            await rpc.request("large", {})

async def test_backlog_overflow_is_explicit(tmp_path):
    async with CodexRpc([sys.executable, "-u", str(FAKE)], cwd=tmp_path, max_events=2) as rpc:
        with pytest.raises(RpcError, match="event_backlog_overflow"):
            await rpc.request("flood", {})

async def test_fragmented_utf8(rpc):
    async with rpc:
        await rpc.request("split", {})
        assert (await rpc.next_event(timeout=1))["params"]["text"] == "\u4e2d\u6587"

async def test_upstream_error_never_exposes_message(rpc):
    async with rpc:
        with pytest.raises(RpcError) as error:
            await rpc.request("error", {})
        assert error.value.code == -32001
        assert "secret" not in str(error.value)

async def test_close_releases_waiters_and_cannot_restart(rpc):
    await rpc.start()
    request = asyncio.create_task(rpc.request("stall", {}))
    event = asyncio.create_task(rpc.next_event())
    await asyncio.sleep(0.03)
    await rpc.close()
    results = await asyncio.gather(request, event, return_exceptions=True)
    assert all(isinstance(item, RpcClosed) for item in results)
    assert not rpc._tasks
    with pytest.raises(RuntimeError):
        await rpc.start()
