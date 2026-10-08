"""Cross-process-style PostgreSQL admission checks; scripted RPC never calls a model."""
import asyncio
from datetime import timedelta

import pytest

from app.core.time import utc_now
from app.design.models import Operation, Wallet
from app.operations.leases import claim, recover_expired
from .test_migrations import migration_database
from .test_operations import factory, quoted, setup, submit_request
from .test_worker import prepared
from .test_reconciliation import HistoryRpc, history


@pytest.mark.parametrize("images", [False, True], ids=["reviewer", "main"])
async def test_another_owner_cannot_open_runtime_while_trial_is_active(factory, tmp_path, images):
    first, runtime, owner, _, first_id = await prepared(factory, tmp_path / "first")
    second, second_runtime, second_owner, _, second_id = await prepared(factory, tmp_path / "second")
    second_runtime.thread_count = 100  # Independent runtimes must not reuse fake native thread IDs.
    entered, release = asyncio.Event(), asyncio.Event()
    connection = runtime.connection

    def held_connection(workspace, *, images: bool):
        rpc = connection(workspace, images=images)
        request = rpc.request

        async def held_request(method, params):
            if method == "turn/start" and images == held_images:
                entered.set()
                await release.wait()
            return await request(method, params)

        rpc.request = held_request
        return rpc

    held_images = images
    runtime.connection = held_connection
    execution = asyncio.create_task(first.run_once())
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert await second.run_once() is None
        assert second_runtime.calls == []
        async with factory() as db:
            waiting = await db.get(Operation, second_id)
            assert waiting.state == "queued" and waiting.fencing_token == 0
            assert waiting.lease_owner is None and waiting.dispatch_intent_at is None
            assert (await db.get(Wallet, second_owner)).reserved == 1
    finally:
        release.set()
        await asyncio.wait_for(execution, timeout=10)
    assert await second.run_once() == second_id
    async with factory() as db:
        assert (await db.get(Operation, first_id)).state == "succeeded"
        assert (await db.get(Wallet, owner)).balance == 4
        assert (await db.get(Wallet, second_owner)).balance == 4


@pytest.mark.parametrize("mode", ["unknown_start", "crash_after_image"])
async def test_restart_and_expiry_do_not_free_unknown_turn_but_verified_history_does(factory, tmp_path, mode):
    first, runtime, owner, _, first_id = await prepared(factory, tmp_path / "first", mode)
    assert await first.run_once() == first_id
    second, second_runtime, _, _, second_id = await prepared(factory, tmp_path / "second")
    second_runtime.thread_count = 100
    async with factory.begin() as db:
        operation = await db.get(Operation, first_id)
        assert operation.state == "reconciling"
        operation.lease_expires_at = utc_now() - timedelta(days=1)
        turn_id = operation.upstream_turn_id or "recovered-turn"
    assert await second.run_once() is None
    assert second_runtime.calls == []
    calls = []
    runtime.connection = lambda *a, **kw: HistoryRpc(history(first_id, turn_id), calls)
    assert await first.reconcile(first_id) == "succeeded"
    assert calls == ["thread/read"]
    assert await second.run_once() == second_id
    async with factory() as db:
        assert (await db.get(Operation, second_id)).state == "succeeded"


@pytest.mark.parametrize("state", ["running", "cancelling", "publishing", "failed", "succeeded", "queued"])
async def test_dispatch_without_terminal_evidence_blocks_other_account_even_if_state_changed(factory, state):
    owner, _, branch = await setup(factory)
    first_id = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    other, _, second_branch = await setup(factory)
    second_id = await submit_request(factory, other, second_branch, await quoted(factory, other, second_branch))
    async with factory.begin() as db:
        first = await db.get(Operation, first_id)
        first.state = state
        first.dispatch_intent_at = utc_now() - timedelta(days=1)
        first.lease_expires_at = utc_now() - timedelta(hours=1)
    async with factory.begin() as db:
        assert await claim(db, "other-process", owner_id=other) is None
    async with factory() as db:
        assert (await db.get(Operation, second_id)).state == "queued"


async def test_expired_reviewer_requires_recovery_before_another_trial(factory):
    owner, _, branch = await setup(factory)
    first_id = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    other, _, second_branch = await setup(factory)
    await submit_request(factory, other, second_branch, await quoted(factory, other, second_branch))
    async with factory.begin() as db:
        first = await claim(db, "old-worker", owner_id=owner)
        assert first.id == first_id
        first.lease_expires_at = utc_now() - timedelta(seconds=1)
    async with factory.begin() as db:
        assert await claim(db, "new-worker", owner_id=other) is None
    async with factory.begin() as db:
        assert await recover_expired(db, first_id) == "queued"
    async with factory.begin() as db:
        assert await claim(db, "new-worker", owner_id=other)


async def test_confirmed_terminal_waiting_for_file_publication_does_not_hold_runtime_slot(factory, tmp_path, monkeypatch):
    first, runtime, _, _, first_id = await prepared(factory, tmp_path / "first")
    import app.operations.worker as module

    async def disk_failure(*args, **kwargs):
        raise OSError("test disk unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(module, "publish", disk_failure)
        assert await first.run_once() == first_id
    async with factory() as db:
        first_operation = await db.get(Operation, first_id)
        assert first_operation.state == "publishing"
        assert first_operation.upstream_status == "completed"
    second, second_runtime, _, _, second_id = await prepared(factory, tmp_path / "second")
    second_runtime.thread_count = 100
    assert await second.run_once() == second_id
    async with factory() as db:
        assert (await db.get(Operation, second_id)).state == "succeeded"
    calls = len(runtime.calls)
    assert await first.retry_publish(first_id) == "succeeded"
    assert len(runtime.calls) == calls


async def test_uncommitted_claim_cannot_race_another_database_connection(factory):
    first_owner, _, first_branch = await setup(factory)
    first_id = await submit_request(factory, first_owner, first_branch, await quoted(factory, first_owner, first_branch))
    second_owner, _, second_branch = await setup(factory)
    second_id = await submit_request(factory, second_owner, second_branch, await quoted(factory, second_owner, second_branch))
    async with factory.begin() as first_db:
        assert (await claim(first_db, "process-a", owner_id=first_owner)).id == first_id
        async with factory.begin() as second_db:
            assert await asyncio.wait_for(claim(second_db, "process-b", owner_id=second_owner), timeout=2) is None
        await first_db.rollback()
    async with factory.begin() as db:
        assert (await claim(db, "process-b", owner_id=second_owner)).id == second_id
