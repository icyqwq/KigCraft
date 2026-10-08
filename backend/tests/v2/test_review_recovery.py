"""Review failure recovery with PostgreSQL and a scripted Codex boundary."""
import pytest
from sqlalchemy import select
from app.codex_runtime.rpc import RpcClosed
from app.contracts.v2 import ContinuationDraft
from app.design.models import Branch, Message, Operation, Wallet
from app.operations.message_edit import prepare_message_edit
from .test_migrations import migration_database
from .test_operations import factory, request, quoted, submit_request
from .test_worker import prepared, ScriptedRpc


@pytest.mark.parametrize("mode", ["outage", "rejected", "injection"])
async def test_review_failure_releases_quota_and_explicit_edit_rechecks_original(factory, tmp_path, monkeypatch, mode):
    original_request = ScriptedRpc.request

    async def interrupted(self, method, params):
        if mode == "outage" and self.runtime.mode == "outage" and not self.images and method == "turn/start":
            self.runtime.calls.append((False, method, params))
            raise RpcClosed()
        return await original_request(self, method, params)

    monkeypatch.setattr(ScriptedRpc, "request", interrupted)
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path, mode)
    assert await worker.run_once() == opid
    assert await worker.run_once() is None
    assert not any(call[0] for call in runtime.calls)
    async with factory() as db:
        failed = await db.get(Operation, opid)
        assert failed.state == "failed"
        assert failed.error_code == {"outage": "moderation_unavailable", "rejected": "moderation_rejected",
                                     "injection": "prompt_injection_rejected"}[mode]
        assert failed.dispatch_intent_at is None
        assert failed.reservation_closed and failed.settled_units == 0
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (5, 0)
        message = await db.scalar(select(Message).where(Message.operation_id == opid, Message.role == "user"))
        source = await prepare_message_edit(db, owner, branch, message.id)
        digest = source.inputs.digest()
        assert source.inputs.user_text == "coral wig"
        current = await db.get(Branch, branch)
        version = current.state_version
    calls = len(runtime.calls)
    retry = await quoted(factory, owner, branch, request(
        client_request_id="review-recovery", text="coral wig", expected_branch_version=version,
        continuation=ContinuationDraft(source_revision_id=None, boundary_message_id=message.id)))
    assert len(runtime.calls) == calls
    retry_id = await submit_request(factory, owner, branch, retry)
    assert retry_id != opid
    assert len(runtime.calls) == calls
    runtime.mode = "image"
    assert await worker.run_once() == retry_id
    assert await worker.run_once() is None
    assert len([c for c in runtime.calls if not c[0] and c[1] == "turn/start"]) == 2
    assert len([c for c in runtime.calls if c[0] and c[1] == "turn/start"]) == 1
    async with factory() as db:
        original = await prepare_message_edit(db, owner, branch, message.id)
        assert original.inputs.digest() == digest
        assert original.operation.state == "failed" and original.operation.settled_units == 0
        recovered = await db.get(Operation, retry_id)
        assert recovered.state == "succeeded" and recovered.settled_units == 1
        assert recovered.source_operation_id == opid and recovered.branch_id != branch
        assert (await db.get(Branch, branch)).current_revision_id is None
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (4, 0)
