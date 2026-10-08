"""Recovery reads only; persisted evidence must match the exact accepted request."""
import pytest
from sqlalchemy import func, select
from app.codex_runtime.history import read_turn
from app.codex_runtime.launch import DESIGN_MODEL
from app.codex_runtime.rpc import RpcError
from app.design.models import Branch, CodexEpoch, LedgerEntry, Message, Operation, Revision, Wallet
from app.operations.cancel import cancel
from app.operations.leases import heartbeat
from .test_operations import factory
from .test_migrations import migration_database
from .test_worker import prepared, png


def history(opid, turn_id="recovered-turn", status="completed"):
    return {"thread": {"id": "main-thread", "model": DESIGN_MODEL, "turns": [{
        "id": turn_id, "status": status, "itemsView": "full", "items": [
            {"id": "user", "type": "userMessage", "clientId": opid, "content": []},
            {"id": "image", "type": "imageGeneration", "status": "completed", "result": png()},
            {"id": "message", "type": "agentMessage", "text": "Your design is ready."},
        ]}]}}


class HistoryRpc:
    def __init__(self, response, calls):
        self.response, self.calls = response, calls
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def request(self, method, params):
        self.calls.append(method)
        assert method == "thread/read"
        assert params == {"threadId": "main-thread", "includeTurns": True}
        return self.response


async def unknown(factory, tmp_path, mode="unknown_start"):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path, mode)
    await worker.run_once()
    async with factory() as db:
        operation = await db.get(Operation, opid)
        response = history(opid, operation.upstream_turn_id or "recovered-turn")
    calls = []
    runtime.connection = lambda *args, **kwargs: HistoryRpc(response, calls)
    return worker, runtime, owner, branch, opid, response, calls


@pytest.mark.parametrize("mode", ["unknown_start", "crash_after_image"])
async def test_reconcile_missing_ack_or_terminal_without_duplicate_delivery(factory, tmp_path, mode):
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path, mode)
    assert await worker.reconcile(opid) == "succeeded"
    assert calls == ["thread/read"]
    async with factory() as db:
        op = await db.get(Operation, opid)
        assert op.reservation_closed and op.upstream_status == "completed"
        assert (await db.get(Wallet, owner)).balance == 4
        assert (await db.get(Wallet, owner)).reserved == 0
        assert (await db.execute(select(func.count()).select_from(Revision))).scalar_one() == 1
        assert (await db.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == "settle"))).scalar_one() == 1
        epoch = await db.get(CodexEpoch, (await db.get(Branch, branch)).active_epoch_id)
        assert epoch.status == "ready" and epoch.checkpoint_turn_id == op.upstream_turn_id


@pytest.mark.parametrize("defect", ["absent", "active", "wrong_client", "partial", "wrong_model", "duplicate"])
async def test_unconfirmed_recovery_keeps_reservation_and_never_dispatches(factory, tmp_path, defect):
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path)
    thread = response["thread"]
    turn = thread["turns"][0]
    if defect == "absent": thread["turns"] = []
    if defect == "active": turn["status"] = "inProgress"
    if defect == "wrong_client": turn["items"][0]["clientId"] = "another-request"
    if defect == "partial": turn["itemsView"] = "summary"
    if defect == "wrong_model": thread["model"] = "other-model"
    if defect == "duplicate": thread["turns"].append(turn.copy())
    assert await worker.reconcile(opid) == "reconciling"
    assert calls == ["thread/read"]
    async with factory() as db:
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (5, 1)
        op = await db.get(Operation, opid)
        assert op.upstream_status is None and op.lease_owner is None


async def test_changed_login_does_not_read_history(factory, tmp_path):
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path)
    async def changed_identity(rpc): return "b" * 64
    runtime.identity = changed_identity
    assert await worker.reconcile(opid) == "reconciling"
    assert calls == []


async def test_cancelled_recovery_discards_unpublished_image(factory, tmp_path):
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path)
    async with factory.begin() as db:
        await cancel(db, owner, opid)
    assert await worker.reconcile(opid) == "cancelled"
    async with factory() as db:
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (5, 0)
        assert (await db.execute(select(func.count()).select_from(Revision))).scalar_one() == 0


async def test_recovery_publication_failure_preserves_bytes_for_retry(factory, tmp_path, monkeypatch):
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path)
    import app.operations.reconcile as module
    async def disk_failure(*args, **kwargs): raise OSError("unavailable")
    monkeypatch.setattr(module, "publish", disk_failure)
    assert await worker.reconcile(opid) == "publishing"
    assert await worker.retry_publish(opid) == "succeeded"
    assert calls == ["thread/read"]
    async with factory() as db:
        assert (await db.get(Wallet, owner)).balance == 4


@pytest.mark.parametrize("status,expected", [("failed","failed"),("interrupted","cancelled"),("completed","failed")])
async def test_terminal_without_image_releases_reservation(factory, tmp_path, status, expected):
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path)
    turn = response["thread"]["turns"][0]
    turn["status"] = status
    turn["items"] = [turn["items"][0]]
    assert await worker.reconcile(opid) == expected
    async with factory() as db:
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (5, 0)


async def test_known_turn_cannot_be_replaced_by_client_id_match():
    data = history("request", "different-turn")
    with pytest.raises(RpcError, match="history_identity_conflict"):
        await read_turn(HistoryRpc(data, []), thread_id="main-thread", turn_id="known-turn", client_request_id="request")


async def test_cancel_after_published_image_retains_one_charge(factory, tmp_path):
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path, "crash_after_image")
    async with factory.begin() as db:
        await cancel(db, owner, opid)
    assert await worker.reconcile(opid) == "partial_success"
    async with factory() as db:
        assert (await db.get(Wallet, owner)).balance == 4
        assert (await db.execute(select(func.count()).select_from(Revision))).scalar_one() == 1


async def test_old_raw_assistant_path_can_be_replayed_without_conflict(factory, tmp_path):
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path, "crash_after_image")
    async with factory.begin() as db:
        message = (await db.execute(select(Message).where(Message.operation_id == opid, Message.role == "assistant"))).scalar_one()
        message.content_parts = [{"type":"text","text":"Your design is ready.\n![image](C:/private/result.png)"}]
    assert await worker.reconcile(opid) == "succeeded"


async def test_changed_lease_fences_recovery_before_any_publication(factory, tmp_path):
    from app.design.service import ApplicationError
    from app.core.time import utc_now
    from datetime import timedelta
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path)
    class FenceRpc(HistoryRpc):
        async def request(self, method, params):
            async with factory.begin() as db:
                op = await db.get(Operation, opid)
                op.fencing_token += 1
                op.lease_owner = "new-owner"
                op.lease_expires_at = utc_now() + timedelta(seconds=90)
            return await super().request(method, params)
    runtime.connection = lambda *args, **kwargs: FenceRpc(response, calls)
    with pytest.raises(ApplicationError, match="stale_lease"):
        await worker.reconcile(opid)
    async with factory() as db:
        assert (await db.get(Operation, opid)).lease_owner == "new-owner"
        assert (await db.get(Wallet, owner)).reserved == 1
        assert (await db.execute(select(func.count()).select_from(Revision))).scalar_one() == 0


async def test_confirmed_terminal_with_invalid_output_releases_without_dispatch(factory,tmp_path):
    worker,runtime,owner,branch,opid,response,calls=await unknown(factory,tmp_path)
    response["thread"]["turns"][0]["items"][1]["result"]="invalid-base64"
    assert await worker.reconcile(opid)=="failed"
    assert calls==["thread/read"]
    async with factory() as db:
        op=await db.get(Operation,opid)
        assert op.upstream_status=="completed" and op.error_code=="image_generation_failed"
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(5,0)


@pytest.mark.parametrize("defect", ["expired", "invalid", "extra"])
async def test_committed_delivery_recovery_does_not_restage_temporary_output(factory, tmp_path, monkeypatch, defect):
    worker, runtime, owner, branch, opid, response, calls = await unknown(factory, tmp_path, "crash_after_image")
    items = response["thread"]["turns"][0]["items"]
    if defect == "expired":
        items[1].pop("result")
        items[1]["savedPath"] = str(tmp_path / "expired.png")
    elif defect == "invalid":
        items[1]["result"] = "invalid-base64"
    else:
        items.append({"id": "extra", "type": "imageGeneration", "status": "failed", "failure": "extra"})
    import app.operations.reconcile as module
    def forbidden(*args, **kwargs):
        pytest.fail("committed delivery must not be restaged")
    monkeypatch.setattr(module, "stage_image", forbidden)
    assert await worker.reconcile(opid) == "succeeded"
    assert calls == ["thread/read"]
    async with factory() as db:
        op = await db.get(Operation, opid)
        assert op.error_code is None and op.upstream_status == "completed"
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (4, 0)
        assert (await db.execute(select(func.count()).select_from(Revision))).scalar_one() == 1
        assert (await db.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == "settle"))).scalar_one() == 1
