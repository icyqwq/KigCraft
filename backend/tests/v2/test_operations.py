import asyncio
import base64
import io
from datetime import timedelta
from uuid import uuid4
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.codex_runtime.artifacts import stage_image
from app.contracts.v2 import SubmitTurn
from app.core.time import utc_now
from app.db.models import User
from app.design.models import Branch, Design, DomainEvent, LedgerEntry, Operation, Revision, Wallet
from app.design.service import ApplicationError, create_design, owned_design, rename_design
from app.operations.cancel import cancel
from app.operations.leases import claim, record_dispatch_intent, recover_expired
from app.operations.publish import publish
from app.operations.service import quote, submit
from app.quota.wallet import entry
from app.generation.quota import current_generation_quota_window
from .test_migrations import migration_database, migrate

@pytest_asyncio.fixture
async def factory(migration_database):
    result = migrate(migration_database)
    assert result.returncode == 0, result.stderr
    engine = create_async_engine(migration_database)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()

def request(**extra):
    return SubmitTurn.model_validate({
        "client_request_id": "request-001", "intent": "generate_or_clarify", "text": "coral wig",
        "expected_branch_version": 0, "quote_id": "placeholder", **extra,
    })

async def setup(factory, balance=5):
    async with factory.begin() as db:
        user = User(email=f"{uuid4()}@example.com")
        db.add(user)
        await db.flush()
        wallet = Wallet(owner_id=user.id, balance=0, reserved=0, version=0, write_owner="v2",
                        last_topup_window=current_generation_quota_window()[0])
        db.add(wallet)
        await db.flush()
        await entry(db, wallet, key="opening", kind="opening", balance_delta=balance, reserved_delta=0)
        design = await create_design(db, user.id, "design-request", "Character")
        return user.id, design.id, design.last_branch_id

async def quoted(factory, owner, branch, payload=None):
    payload = payload or request()
    async with factory.begin() as db:
        price = await quote(db, owner, branch, payload)
        return payload.model_copy(update={"quote_id": price.id})

async def submit_request(factory, owner, branch, payload):
    async with factory.begin() as db:
        return (await submit(db, owner, branch, payload)).id

async def running(factory, operation_id):
    async with factory.begin() as db:
        operation = await claim(db, "worker")
        assert operation.id == operation_id
        token = operation.fencing_token
    async with factory.begin() as db:
        await record_dispatch_intent(db, operation_id, "worker", token, "codex-thread")
    return token

def staged(tmp_path):
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "coral").save(output, format="PNG")
    return stage_image({"id": "image", "type": "imageGeneration", "status": "completed",
                        "result": base64.b64encode(output.getvalue()).decode()},
                       runtime_id="runtime", thread_id="thread", turn_id="turn",
                       output_root=tmp_path, staging_root=tmp_path / "staging")

async def test_duplicate_submission_reserves_once(factory):
    owner, design, branch = await setup(factory)
    payload = await quoted(factory, owner, branch)
    first, second = await asyncio.gather(
        submit_request(factory, owner, branch, payload), submit_request(factory, owner, branch, payload))
    assert first == second
    async with factory() as db:
        assert (await db.get(Wallet, owner)).reserved == 1
        assert (await db.execute(select(func.count()).select_from(Operation))).scalar_one() == 1
        assert (await db.execute(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.kind == "reserve"))).scalar_one() == 1

async def test_same_key_different_request_conflicts(factory):
    owner, _, branch = await setup(factory)
    payload = await quoted(factory, owner, branch)
    await submit_request(factory, owner, branch, payload)
    with pytest.raises(ApplicationError, match="idempotency_conflict"):
        await submit_request(factory, owner, branch, payload.model_copy(update={"text": "changed"}))

async def test_quote_cannot_authorize_changed_text(factory):
    owner, _, branch = await setup(factory)
    payload = await quoted(factory, owner, branch)
    with pytest.raises(ApplicationError, match="quote_invalid"):
        await submit_request(factory, owner, branch, payload.model_copy(update={"text": "new request"}))
    async with factory() as db:
        assert (await db.get(Wallet, owner)).reserved == 0

async def test_cross_design_concurrency_cannot_double_spend(factory):
    owner, _, branch1 = await setup(factory, balance=1)
    async with factory.begin() as db:
        branch2 = (await create_design(db, owner, "second-design", "Other")).last_branch_id
    first = await quoted(factory, owner, branch1)
    second = await quoted(factory, owner, branch2, request(client_request_id="request-002"))
    results = await asyncio.gather(submit_request(factory, owner, branch1, first),
                                   submit_request(factory, owner, branch2, second), return_exceptions=True)
    assert sum(isinstance(r, ApplicationError) and r.code == "quota_insufficient" for r in results) == 1
    async with factory() as db:
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (1, 1)
        assert (await db.execute(select(func.count()).select_from(Operation))).scalar_one() == 1

async def test_publish_replay_is_one_revision_one_settlement(factory, tmp_path):
    owner, _, branch = await setup(factory)
    operation = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    token = await running(factory, operation)
    image = staged(tmp_path)
    async with factory.begin() as db:
        first = await publish(db, operation, "worker", token, "main_image", image, storage_root=tmp_path / "objects")
    async with factory.begin() as db:
        second = await publish(db, operation, "worker", token, "main_image", image, storage_root=tmp_path / "objects")
    assert first.id == second.id
    assert first.number == second.number == 1
    async with factory() as db:
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (4, 0)
        assert (await db.get(Branch, branch)).current_revision_id == first.id
        assert (await db.execute(select(func.count()).select_from(Revision))).scalar_one() == 1
        assert (await db.execute(select(func.sum(LedgerEntry.balance_delta)))).scalar_one() == 4

async def test_expired_dispatched_operation_is_reconciled_never_requeued(factory, tmp_path):
    owner, _, branch = await setup(factory)
    operation_id = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    token = await running(factory, operation_id)
    async with factory.begin() as db:
        operation = await db.get(Operation, operation_id)
        operation.lease_expires_at = utc_now() - timedelta(seconds=1)
    async with factory.begin() as db:
        assert await recover_expired(db, operation_id) == "reconciling"
        assert await claim(db, "worker-2") is None
    with pytest.raises(ApplicationError, match="stale_lease"):
        async with factory.begin() as db:
            await publish(db, operation_id, "worker", token, "main_image", staged(tmp_path), storage_root=tmp_path / "objects")

async def test_queued_cancel_releases_once(factory):
    owner, _, branch = await setup(factory)
    operation = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    for _ in range(2):
        async with factory.begin() as db:
            assert await cancel(db, owner, operation) == "cancelled"
    async with factory() as db:
        assert (await db.get(Wallet, owner)).reserved == 0
        assert (await db.get(Wallet, owner)).balance == 5
        assert (await db.execute(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.kind == "release"))).scalar_one() == 1

async def test_cancel_wins_before_publication(factory, tmp_path):
    owner, _, branch = await setup(factory)
    operation = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    token = await running(factory, operation)
    async with factory.begin() as db:
        assert await cancel(db, owner, operation) == "cancelling"
    with pytest.raises(ApplicationError, match="operation_not_publishable"):
        async with factory.begin() as db:
            await publish(db, operation, "worker", token, "main_image", staged(tmp_path), storage_root=tmp_path / "objects")
    async with factory() as db:
        assert (await db.get(Wallet, owner)).balance == 5

async def test_late_result_does_not_move_changed_branch(factory, tmp_path):
    owner, design, branch = await setup(factory)
    operation = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    token = await running(factory, operation)
    async with factory.begin() as db:
        (await db.get(Branch, branch)).state_version += 1
    async with factory.begin() as db:
        await publish(db, operation, "worker", token, "main_image", staged(tmp_path), storage_root=tmp_path / "objects")
    async with factory() as db:
        assert (await db.get(Branch, branch)).current_revision_id is None
        events = (await db.execute(select(DomainEvent).where(DomainEvent.design_id == design).order_by(DomainEvent.sequence))).scalars().all()
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        assert next(e.payload for e in events if e.event_type == "image.ready")["is_current"] is False

async def test_view_and_rename_do_not_change_content_version_or_cross_owner(factory):
    owner, design, branch = await setup(factory)
    other, _, _ = await setup(factory)
    async with factory.begin() as db:
        await owned_design(db, owner, design)
        await rename_design(db, owner, design, "Renamed", 0)
        assert (await db.get(Branch, branch)).state_version == 0
    with pytest.raises(ApplicationError, match="not_found"):
        async with factory() as db:
            await owned_design(db, other, design)

async def test_ledger_is_database_immutable(factory):
    owner, _, _ = await setup(factory)
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match="immutable V2 record"):
        async with factory.begin() as db:
            await db.execute(text("UPDATE v2_ledger SET balance_delta=999 WHERE owner_id=:owner"), {"owner": owner})

async def test_concurrent_workers_claim_one_local_trial_operation(factory):
    owner, _, branch1 = await setup(factory)
    async with factory.begin() as db:
        branch2 = (await create_design(db, owner, "claim-design-2", "Other")).last_branch_id
    first = await submit_request(factory, owner, branch1, await quoted(factory, owner, branch1))
    second = await submit_request(factory, owner, branch2, await quoted(factory, owner, branch2,
        request(client_request_id="claim-request-2")))
    async def take(name):
        async with factory.begin() as db:
            operation = await claim(db, name)
            return operation.id if operation else None
    ids = await asyncio.gather(take("worker-a"), take("worker-b"))
    assert len([identity for identity in ids if identity]) == 1
    assert next(identity for identity in ids if identity) in {first, second}
    async with factory.begin() as db:
        assert await claim(db, "worker-c") is None
    async with factory() as db:
        operations = (await db.scalars(select(Operation))).all()
        assert sorted(op.state for op in operations) == ["queued", "reviewing"]

async def test_expired_known_completed_turn_remains_publishable(factory):
    owner, _, branch = await setup(factory)
    operation_id = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    await running(factory, operation_id)
    async with factory.begin() as db:
        operation = await db.get(Operation, operation_id)
        operation.upstream_status = "completed"
        operation.state = "publishing"
        operation.lease_expires_at = utc_now() - timedelta(seconds=1)
    async with factory.begin() as db:
        assert await recover_expired(db, operation_id) == "publishing"
        assert await claim(db, "worker-2") is None

@pytest.mark.parametrize("limit", ["count", "bytes"])
async def test_reference_capacity_is_rejected_at_quote_before_reserving(factory, monkeypatch, limit):
    from app.core.config import get_settings
    from app.design.models import Asset
    monkeypatch.setenv("V2_MAX_INPUT_IMAGES" if limit == "count" else "V2_MAX_INPUT_BYTES", "2")
    get_settings.cache_clear()
    try:
        owner, _, branch = await setup(factory)
        async with factory.begin() as db:
            assets = [Asset(owner_id=owner,object_key=f"ref-{i}.png",sha256=str(i)*64,mime_type="image/png",
                kind="reference",width=16,height=16,byte_size=1,status="ready") for i in range(3)]
            db.add_all(assets)
            await db.flush()
        payload = request(attachments=[{"asset_id":item.id,"usage":""} for item in assets])
        with pytest.raises(ApplicationError,match="reference_limit_exceeded"):
            async with factory.begin() as db:
                await quote(db, owner, branch, payload)
        async with factory() as db:
            assert (await db.get(Wallet, owner)).reserved == 0
            assert (await db.execute(select(func.count()).select_from(Operation))).scalar_one() == 0
    finally:
        get_settings.cache_clear()
