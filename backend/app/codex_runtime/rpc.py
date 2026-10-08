"""One reader for all RPC responses, notifications and server requests.

This transport is private to the backend. Raw frames are never UI events.
A lost connection/timeout after a write has an unknown execution outcome:
callers must reconcile rather than retry a turn/start.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import hashlib
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

Frame = dict[str, Any]
RequestHandler = Callable[[str, Frame], Awaitable[Frame]]


class RpcError(RuntimeError):
    def __init__(self, code: int | str, method: str = "") -> None:
        self.code = code
        self.method = method
        # Upstream messages can contain credentials, paths and user data.
        super().__init__(f"Codex RPC {method or 'transport'} failed ({code})")


class RpcClosed(RpcError):
    def __init__(self) -> None:
        super().__init__("connection_closed")


class RpcTimeout(RpcError):
    def __init__(self, method: str) -> None:
        super().__init__("outcome_unknown", method)


async def deny_server_request(method: str, params: Frame) -> Frame:
    """No model request can implicitly grant filesystem/network permission."""
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        return {"decision": "decline"}
    if method in {"execCommandApproval", "applyPatchApproval"}:
        return {"decision": "denied"}
    if method == "item/permissions/requestApproval":
        return {"permissions": {}, "scope": "turn"}
    raise RpcError(-32601, method)


class CodexRpc:
    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        request_handler: RequestHandler = deny_server_request,
        timeout: float = 30,
        max_frame_bytes: int = 32 * 1024 * 1024,
        max_event_bytes: int = 64 * 1024 * 1024,
        max_events: int = 2048,
        max_server_requests: int = 2048,
        max_server_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if not command or timeout <= 0 or min(max_frame_bytes, max_event_bytes, max_events,
                max_server_requests, max_server_response_bytes) <= 0:
            raise ValueError("Invalid RPC transport limits")
        self.command = tuple(command)
        self.cwd = cwd.resolve()
        self.env = dict(env) if env is not None else None
        self.request_handler = request_handler
        self.timeout = timeout
        self.max_frame_bytes = max_frame_bytes
        self.max_event_bytes = max_event_bytes
        self.max_events = max_events
        self.max_server_requests = max_server_requests
        self.max_server_response_bytes = max_server_response_bytes
        # Connection-local only: a request ID must never be replayed on a new process.
        self._server_requests: dict[str | int, tuple[bytes, bytes | None]] = {}
        self._server_response_bytes = 0
        self._server_tasks: set[asyncio.Task[Any]] = set()
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[Frame]] = {}
        self._write_lock = asyncio.Lock()
        self._events: deque[tuple[Frame, int]] = deque()
        self._event_available = asyncio.Event()
        self._event_bytes = 0
        self._closed = asyncio.Event()
        self._failure: RpcError | None = None
        self._next_id = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self.stderr_bytes = 0
        self.late_responses = 0

    async def __aenter__(self) -> CodexRpc:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def _task(self, awaitable: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _server_task(self, awaitable: Awaitable[Any]) -> None:
        task = self._task(awaitable)
        self._server_tasks.add(task)
        task.add_done_callback(self._server_tasks.discard)

    async def start(self) -> Frame:
        if self._process is not None or self._closed.is_set():
            raise RuntimeError("RPC instances cannot be restarted")
        self._process = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.cwd, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=self.max_frame_bytes,
        )
        self._task(self._read_stdout())
        self._task(self._drain_stderr())
        try:
            result = await self.request("initialize", {
                "clientInfo": {"name": "kigcraft-v2", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            await self.notify("initialized", {})
            return result
        except BaseException:
            await self.close()
            raise

    def _fail(self, error: RpcError) -> None:
        if self._closed.is_set():
            return
        self._failure = error
        self._closed.set()
        self._event_available.set()
        current = asyncio.current_task()
        for task in tuple(self._server_tasks):
            if task is not current:
                task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    async def _send(self, frame: Frame) -> None:
        if self._closed.is_set() or self._process is None:
            raise self._failure or RpcClosed()
        raw = json.dumps(frame, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        await self._send_bytes(raw)

    async def _send_bytes(self, raw: bytes) -> None:
        if len(raw) > self.max_frame_bytes:
            raise RpcError("outgoing_frame_too_large")
        async with self._write_lock:
            if self._closed.is_set():
                raise self._failure or RpcClosed()
            assert self._process.stdin is not None
            try:
                self._process.stdin.write(raw)
                await self._process.stdin.drain()
            except (BrokenPipeError, ConnectionError):
                self._fail(RpcClosed())
                raise RpcClosed() from None

    async def request(self, method: str, params: Frame, *, timeout: float | None = None) -> Frame:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(timeout if timeout is not None else self.timeout):
                await self._send({"id": request_id, "method": method, "params": params})
                return await future
        except TimeoutError:
            raise RpcTimeout(method) from None
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Consume failure if sending itself raised.

    async def notify(self, method: str, params: Frame) -> None:
        await self._send({"method": method, "params": params})

    async def next_event(self, *, timeout: float | None = None) -> Frame:
        # Pop only after a cancellable wait: cancellation cannot consume an event.
        async with asyncio.timeout(timeout):
            while True:
                if self._closed.is_set():
                    raise self._failure or RpcClosed()
                if self._events:
                    event, size = self._events.popleft()
                    self._event_bytes -= size
                    return event
                self._event_available.clear()
                await self._event_available.wait()

    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                raw = await self._process.stdout.readline()
                if not raw:
                    raise RpcClosed()
                if len(raw) > self.max_frame_bytes:
                    raise RpcError("incoming_frame_too_large")
                if not raw.strip():
                    continue
                try:
                    frame = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise RpcError("invalid_frame") from None
                if not isinstance(frame, dict):
                    raise RpcError("invalid_frame")
                if "method" in frame:
                    if not isinstance(frame["method"], str) or not isinstance(frame.get("params", {}), dict):
                        raise RpcError("invalid_frame")
                    if "id" in frame:
                        if not isinstance(frame["id"], (str, int)) or isinstance(frame["id"], bool):
                            raise RpcError("invalid_request_id")
                        self._accept_server_request(frame)
                    else:
                        if len(self._events) >= self.max_events or self._event_bytes + len(raw) > self.max_event_bytes:
                            raise RpcError("event_backlog_overflow")
                        self._event_bytes += len(raw)
                        self._events.append((frame, len(raw)))
                        self._event_available.set()
                elif "id" in frame:
                    request_id = frame["id"]
                    if not isinstance(request_id, int) or isinstance(request_id, bool):
                        raise RpcError("invalid_response_id")
                    future = self._pending.get(request_id)
                    if future is None or future.done():
                        self.late_responses += 1
                        continue
                    if "error" in frame and isinstance(frame["error"], dict):
                        future.set_exception(RpcError(frame["error"].get("code", "upstream_error")))
                    elif isinstance(frame.get("result"), dict):
                        future.set_result(frame["result"])
                    else:
                        raise RpcError("invalid_response")
                else:
                    raise RpcError("invalid_frame")
        except asyncio.CancelledError:
            raise
        except RpcError as exc:
            self._fail(exc)
        except (ValueError, OSError):
            self._fail(RpcError("invalid_or_oversized_stream"))

    def _accept_server_request(self, frame: Frame) -> None:
        request_id = frame["id"]
        fingerprint = hashlib.sha256(json.dumps(
            [frame["method"], frame.get("params", {})], sort_keys=True,
            ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")).digest()
        previous = self._server_requests.get(request_id)
        if previous is not None:
            if previous[0] != fingerprint:
                raise RpcError("server_request_identity_conflict")
            if previous[1] is None:
                return  # One handler and one eventual reply for concurrent duplicates.
            if len(self._server_tasks) >= 64:
                raise RpcError("too_many_server_requests")
            self._server_task(self._replay_server_response(previous[1]))
            return
        if len(self._server_requests) >= self.max_server_requests:
            # Do not evict IDs and accidentally run an old request a second time.
            raise RpcError("server_request_history_overflow")
        if len(self._server_tasks) >= 64:
            raise RpcError("too_many_server_requests")
        self._server_requests[request_id] = (fingerprint, None)
        self._server_task(self._answer(frame))

    async def _replay_server_response(self, raw: bytes) -> None:
        try:
            await self._send_bytes(raw)
        except RpcError as exc:
            self._fail(exc)

    async def _answer(self, frame: Frame) -> None:
        try:
            try:
                async with asyncio.timeout(self.timeout):
                    result = await self.request_handler(frame["method"], frame.get("params", {}))
                if not isinstance(result, dict):
                    raise ValueError("Invalid handler result")
                raw = json.dumps({"id": frame["id"], "result": result},
                                 ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
            except Exception:
                # No exception text, user input or credential-bearing diagnostic escapes.
                raw = json.dumps({"id": frame["id"], "error": {
                    "code": -32601, "message": "Request not permitted",
                }}).encode("utf-8") + b"\n"
            if self._closed.is_set():
                return
            if len(raw) > self.max_frame_bytes:
                raise RpcError("outgoing_frame_too_large")
            if self._server_response_bytes + len(raw) > self.max_server_response_bytes:
                raise RpcError("server_response_history_overflow")
            fingerprint, _ = self._server_requests[frame["id"]]
            self._server_requests[frame["id"]] = (fingerprint, raw)
            self._server_response_bytes += len(raw)
            await self._send_bytes(raw)
        except RpcError as exc:
            self._fail(exc)

    async def _drain_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        # Count and discard. A raw diagnostic tail could leak auth/prompt data.
        with contextlib.suppress(OSError):
            while chunk := await self._process.stderr.read(8192):
                self.stderr_bytes += len(chunk)

    async def close(self) -> None:
        self._fail(RpcClosed())
        if self._process is not None:
            if self._process.stdin is not None:
                self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=2)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        self._process.kill()
                    await self._process.wait()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._server_tasks.clear()
        self._server_requests.clear()
        self._server_response_bytes = 0
