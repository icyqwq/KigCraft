"""Late worker callbacks cannot change a replacement owner's state.

Real PostgreSQL transactions; scripted RPC is used only to place a takeover
at deterministic asynchronous boundaries. No external model calls.
"""
from datetime import timedelta

import pytest

from app.conversation.fork import allocate
from app.core.time import utc_now
from app.design.models import Operation
from app.design.service import ApplicationError
from app.operations.leases import claim, heartbeat, record_dispatch_intent, recover_expired
from app.operations.lifecycle import finish, message_completed, message_delta, turn_started
from app.operations.observations import observer
from app.operations.publish import publish
from .test_legacy_inventory import fingerprints
from .test_migrations import migration_database
from .test_operations import factory, staged
from .test_worker import ScriptedRpc, prepared


async def transfer(factory, opid):
    # Expiry/recovery revokes the original fence before the replacement gets
    # a lease, matching the persisted recovery ownership protocol.
    async with factory.begin() as db:
        operation = await db.get(Operation, opid)
        operation.lease_expires_at = utc_now() - timedelta(seconds=1)
    async with factory.begin() as db:
        state = await recover_expired(db, opid)
    if state == "queued":
        async with factory.begin() as db:
            current = await claim(db, "replacement")
            assert current.id == opid
    else:
        assert state in {"reconciling", "publishing"}
        async with factory.begin() as db:
            current = await db.get(Operation, opid)
            current.fencing_token += 1
            current.lease_owner = "replacement"
            current.lease_expires_at = utc_now() + timedelta(seconds=90)
    return await fingerprints(factory)


@pytest.mark.parametrize("ownership", ["expired", "transferred"])
@pytest.mark.parametrize("callback", [
    "heartbeat", "dispatch", "started", "delta", "message", "finish",
    "publish", "observation", "allocate",
])
async def test_stale_callbacks_preserve_all_database_rows(factory, tmp_path, ownership, callback):
    worker, runtime, _, _, opid = await prepared(factory, tmp_path)
    async with factory.begin() as db:
        operation = await claim(db, worker.worker_id)
        token = operation.fencing_token
    if ownership == "expired":
        async with factory.begin() as db:
            operation = await db.get(Operation, opid)
            operation.lease_expires_at = utc_now() - timedelta(seconds=1)
        before = await fingerprints(factory)
    else:
        before = await transfer(factory, opid)
    image = staged(tmp_path)
    with pytest.raises(ApplicationError, match="stale_lease"):
        if callback == "observation":
            await observer(worker, opid, token, "generation", "test-model")(
                {"kind": "dispatch", "thread_id": "late-thread"})
        elif callback == "allocate":
            await allocate(worker, ScriptedRpc(runtime, tmp_path, True), opid, token, "a" * 64)
        else:
            async with factory.begin() as db:
                if callback == "heartbeat":
                    await heartbeat(db, opid, worker.worker_id, token)
                elif callback == "dispatch":
                    await record_dispatch_intent(db, opid, worker.worker_id, token, "late-thread")
                elif callback == "started":
                    await turn_started(db, opid, worker.worker_id, token, "late-turn")
                elif callback == "delta":
                    await message_delta(db, opid, worker.worker_id, token, "late-turn",
                                        {"id": "late-item", "text": "late prefix"})
                elif callback == "message":
                    await message_completed(db, opid, worker.worker_id, token, "late-turn",
                                            {"id": "late-item", "text": "late completed reply"})
                elif callback == "finish":
                    await finish(db, opid, worker.worker_id, token, upstream_status="completed")
                elif callback == "publish":
                    await publish(db, opid, worker.worker_id, token, "main_image", image,
                                  storage_root=tmp_path / "assets")
    assert await fingerprints(factory) == before
    assert runtime.calls == []
    assert not (tmp_path / "assets" / "masters").exists()


@pytest.mark.parametrize("boundary", [
    "review_terminal", "allocation_ack", "turn_ack",
    "message_delta", "staged_image", "before_finish",
])
async def test_running_old_worker_cannot_write_after_takeover(factory, tmp_path, monkeypatch, boundary):
    import app.operations.worker as module

    worker, runtime, _, _, opid = await prepared(factory, tmp_path)
    captured = None

    async def takeover():
        nonlocal captured
        assert captured is None, "Fault should happen exactly once"
        captured = await transfer(factory, opid)

    original_request = ScriptedRpc.request
    original_next = ScriptedRpc.next_event

    async def request(self, method, params):
        result = await original_request(self, method, params)
        if self.images and method == "thread/start" and boundary == "allocation_ack":
            await takeover()
        if self.images and method == "turn/start":
            if boundary == "turn_ack":
                await takeover()
            if boundary == "message_delta":
                self.events.insert(0, {"method": "item/agentMessage/delta", "params": {
                    "threadId": self.thread, "turnId": self.turn, "itemId": "late-message",
                    "delta": "This stale prefix must not be persisted."}})
        return result

    async def next_event(self):
        event = await original_next(self)
        if ((not self.images and boundary == "review_terminal" and event["method"] == "turn/completed")
                or (self.images and boundary == "message_delta" and event["method"] == "item/agentMessage/delta")):
            await takeover()
        return event

    monkeypatch.setattr(ScriptedRpc, "request", request)
    monkeypatch.setattr(ScriptedRpc, "next_event", next_event)

    if boundary == "staged_image":
        original_thread = module.asyncio.to_thread

        async def to_thread(function, *args, **kwargs):
            result = await original_thread(function, *args, **kwargs)
            if function is module.stage_image:
                await takeover()
            return result

        monkeypatch.setattr(module.asyncio, "to_thread", to_thread)

    if boundary == "before_finish":
        original_execute = worker._execute

        async def execute(*args):
            result = await original_execute(*args)
            await takeover()
            return result

        monkeypatch.setattr(worker, "_execute", execute)

    assert await worker.run_once() == opid
    assert captured is not None
    assert await fingerprints(factory) == captured
    async with factory() as db:
        current = await db.get(Operation, opid)
        assert current.lease_owner == "replacement"
    generation = [c for c in runtime.calls if c[0] and c[1] == "turn/start"]
    assert len(generation) == (0 if boundary in {"review_terminal", "allocation_ack"} else 1)
    assert not any(c[1] == "turn/interrupt" for c in runtime.calls)
