"""Real PostgreSQL state transitions around a scripted runtime; never calls a paid model."""
import base64
import json
import io
from pathlib import Path
import pytest
from PIL import Image
from sqlalchemy import func, select
from app.codex_runtime.rpc import RpcClosed
from app.codex_runtime.launch import TESTED_CODEX_VERSION
from app.core.config import Settings
from app.design.models import Branch, CodexEpoch, LedgerEntry, Message, Operation, Revision, Wallet
from app.operations.cancel import cancel
from app.operations.worker import LocalWorker
from .test_operations import factory, setup, quoted, request, submit_request
from .test_migrations import migration_database


def png():
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "coral").save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


class ScriptedRpc:
    def __init__(self, runtime, cwd, images):
        self.runtime, self.cwd, self.images = runtime, cwd, images
        self.command = ["codex", "app-server", "features.image_generation=" + str(images).lower()]
        self.events = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def request(self, method, params):
        self.runtime.calls.append((self.images, method, params))
        if method in {"thread/start", "thread/resume", "thread/fork"}:
            if method == "thread/resume":
                thread_id = params["threadId"]
            elif self.images:
                self.runtime.thread_count += 1
                thread_id = "main-thread" if self.runtime.thread_count == 1 else "main-thread-" + str(self.runtime.thread_count)
            else:
                thread_id = "review-thread"
            return {"model": params["model"], "thread": {"id": thread_id}}
        if method == "turn/interrupt":
            self.events = [self.complete("interrupted")]
            return {}
        if method == "turn/start":
            if self.images and self.runtime.mode == "unknown_start":
                raise RpcClosed()
            self.thread = params["threadId"]
            self.turn = "turn-" + str(len(self.runtime.calls))
            if self.images:
                if self.runtime.mode not in {"clarification", "cancel"}:
                    self.events.append(self.item({"id": "image", "type": "imageGeneration",
                        "status": "completed", "result": png()}))
                self.events.append(self.item({"id": "message", "type": "agentMessage",
                    "text": json.dumps({"schema_version":1,"kind":"question","text":"Which color?"}) if self.runtime.mode == "clarification" else "Your design is ready.",
                    "phase": "final_answer"}))
                if self.runtime.mode == "crash_after_image":
                    self.events.append(RpcClosed())
                else:
                    self.events.append(self.complete("completed"))
                if self.runtime.before_ack:
                    await self.runtime.before_ack()
            else:
                verdict = "reject" if self.runtime.mode in {"rejected", "injection"} else "allow"
                category = {"rejected": "unsafe", "injection": "injection"}.get(self.runtime.mode, "none")
                response = {"verdict": verdict, "category": category, "reason": ""}
                payload = json.loads(params["input"][0]["text"])
                if payload.get("asset_checks"):
                    response["asset_decisions"] = [{"ordinal":n,"verdict":"allow"} for n in payload["asset_checks"]]
                self.events = [self.item({"id": "review", "type": "agentMessage",
                    "text": json.dumps(response), "phase": "final_answer"}),
                    self.complete("completed")]
            return {"turn": {"id": self.turn}}
        raise AssertionError(method)

    def item(self, item):
        return {"method": "item/completed", "params": {
            "threadId": self.thread, "turnId": self.turn, "item": item}}

    def complete(self, status):
        return {"method": "turn/completed", "params": {
            "threadId": self.thread, "turn": {"id": self.turn, "status": status}}}

    async def next_event(self):
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


class ScriptedRuntime:
    version = TESTED_CODEX_VERSION
    def __init__(self, mode="image"):
        self.mode, self.calls, self.before_ack = mode, [], None
        self.thread_count = 0
    def connection(self, workspace, *, images):
        return ScriptedRpc(self, workspace, images)
    async def identity(self, rpc):
        return "a" * 64


async def prepared(factory, tmp_path, mode="image"):
    owner, design, branch = await setup(factory)
    operation_id = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    settings = Settings(_env_file=None, app_env="test", v2_enabled=True, v2_new_operations=True,
        v2_runtime_mode="local", v2_local_user_id=owner,
        v2_runtime_dir=str(tmp_path / "runtime"), v2_storage_dir=str(tmp_path / "assets"))
    runtime = ScriptedRuntime(mode)
    return LocalWorker(factory, settings, runtime=runtime), runtime, owner, branch, operation_id


async def test_worker_publishes_charges_and_persists_epoch(factory, tmp_path):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path)
    assert await worker.run_once() == opid
    async with factory() as db:
        op = await db.get(Operation, opid)
        assert op.state == "succeeded"
        assert op.upstream_status == "completed"
        assert op.upstream_turn_id
        assert op.reservation_closed
        assert (await db.get(Wallet, owner)).balance == 4
        current = await db.get(Branch, branch)
        assert current.current_revision_id
        epoch = await db.get(CodexEpoch, current.active_epoch_id)
        assert epoch.status == "ready"
        assert epoch.checkpoint_turn_id == op.upstream_turn_id
        assert (await db.execute(select(func.count()).select_from(Message))).scalar_one() == 2
    assert len([c for c in runtime.calls if c[0] and c[1] == "turn/start"]) == 1


async def test_worker_restart_resumes_persisted_epoch_with_exact_base(factory, tmp_path):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path)
    await worker.run_once()
    async with factory() as db:
        current = await db.get(Branch, branch)
        payload = request(client_request_id="request-second", base_revision_id=current.current_revision_id,
                          expected_branch_version=current.state_version, text="Slight smile")
    second_id = await submit_request(factory, owner, branch, await quoted(factory, owner, branch, payload))
    restarted = LocalWorker(factory, worker.settings, runtime=runtime)
    assert await restarted.run_once() == second_id
    assert any(c[0] and c[1] == "thread/resume" and c[2]["threadId"] == "main-thread" for c in runtime.calls)
    calls = [c for c in runtime.calls if c[0] and c[1] == "turn/start"]
    assert len(calls) == 2
    assert any(i["type"] == "localImage" for i in calls[1][2]["input"])
    async with factory() as db:
        assert (await db.get(Wallet, owner)).balance == 3


@pytest.mark.parametrize("mode,state,expected_balance", [
    ("clarification", "awaiting_input", 5), ("rejected", "failed", 5), ("injection", "failed", 5),
    ("unknown_start", "reconciling", 5), ("crash_after_image", "reconciling", 4),
])
async def test_worker_failure_boundaries(factory, tmp_path, mode, state, expected_balance):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path, mode)
    await worker.run_once()
    assert await worker.run_once() is None
    async with factory() as db:
        operation = await db.get(Operation, opid)
        assert operation.state == state
        wallet = await db.get(Wallet, owner)
        assert wallet.balance == expected_balance
        assert wallet.reserved == (1 if mode == "unknown_start" else 0)
        if mode == "crash_after_image":
            assert (await db.get(Branch, branch)).current_revision_id
        if mode in {"rejected", "injection"}:
            # Rejected in review: the image runtime is never reached and nothing is charged.
            assert not any(c[0] for c in runtime.calls)
            assert operation.error_code == ("prompt_injection_rejected" if mode == "injection" else "moderation_rejected")


async def test_cancel_ack_releases_without_image_or_charge(factory, tmp_path):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path, "cancel")
    async def cancel_before_ack():
        async with factory.begin() as db:
            await cancel(db, owner, opid)
    runtime.before_ack = cancel_before_ack
    await worker.run_once()
    async with factory() as db:
        assert (await db.get(Operation, opid)).state == "cancelled"
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (5, 0)
    assert len([c for c in runtime.calls if c[1] == "turn/interrupt"]) == 1


async def test_failed_publication_retries_existing_bytes_without_model(factory, tmp_path, monkeypatch):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path)
    import app.operations.worker as module
    real_publish = module.publish
    async def failed_publish(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr(module, "publish", failed_publish)
    await worker.run_once()
    async with factory() as db:
        operation = await db.get(Operation, opid)
        assert operation.state == "publishing"
        assert operation.upstream_status == "completed"
        assert operation.staged_outputs["main_image"]["sha256"]
        assert (await db.get(Wallet, owner)).reserved == 1
    calls = len(runtime.calls)
    monkeypatch.setattr(module, "publish", real_publish)
    assert await worker.retry_publish(opid) == "succeeded"
    assert len(runtime.calls) == calls
    async with factory() as db:
        assert (await db.get(Wallet, owner)).balance == 4
        assert (await db.execute(select(func.count()).select_from(Revision))).scalar_one() == 1


async def test_local_worker_cannot_claim_other_account(factory, tmp_path):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path)
    worker.settings.v2_local_user_id = "different-account"
    assert await worker.run_once() is None
    assert runtime.calls == []
