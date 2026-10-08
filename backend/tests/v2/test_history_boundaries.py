from sqlalchemy import func, select
import pytest
from app.design.continuation import continuation_plan
from app.design.manual import SaveManualRevision, save_manual
from app.design.models import Branch, CodexEpoch, Operation, Revision
from app.design.service import ApplicationError
from app.operations.regenerate import regeneration_plan
from .test_migrations import migration_database
from .test_operations import factory, quoted, request, setup, submit_request
from .test_worker import prepared


async def revision_for(factory, opid):
    async with factory() as db:
        return (await db.execute(select(Revision).where(Revision.operation_id == opid))).scalar_one()


async def next_turn(factory, worker, owner, branch_id, text, key):
    async with factory() as db:
        branch = await db.get(Branch, branch_id)
        payload = request(client_request_id=key,text=text,base_revision_id=branch.current_revision_id,
                          expected_branch_version=branch.state_version)
    opid = await submit_request(factory, owner, branch_id, await quoted(factory, owner, branch_id, payload))
    assert await worker.run_once() == opid
    return opid, await revision_for(factory, opid)


async def test_retry_second_of_three_uses_original_base_prompt_and_previous_turn(factory, tmp_path):
    worker, runtime, owner, branch, first = await prepared(factory, tmp_path)
    await worker.run_once()
    original = await revision_for(factory, first)
    second, selected = await next_turn(factory, worker, owner, branch, "Only change the mouth", "second-request")
    third, latest = await next_turn(factory, worker, owner, branch, "Future violet wig", "third-request")
    calls = len(runtime.calls)
    async with factory() as db:
        plan = await regeneration_plan(db, owner, selected.id)
        first_op = await db.get(Operation, first)
        assert plan.boundary.turn_id == first_op.upstream_turn_id
        assert plan.snapshot.user_text == "Only change the mouth"
        assert plan.snapshot.base_revision_id == original.id
        assert plan.snapshot.references[0].asset_id == original.clean_asset_id
        assert plan.source_operation_id == second
        assert (await db.get(Branch, branch)).current_revision_id == latest.id
        assert (await db.execute(select(func.count()).select_from(Branch))).scalar_one() == 1
        assert (await db.execute(select(func.count()).select_from(CodexEpoch))).scalar_one() == 1
    assert len(runtime.calls) == calls


async def test_retry_first_has_fresh_context_and_original_empty_base(factory, tmp_path):
    worker, runtime, owner, branch, first = await prepared(factory, tmp_path)
    await worker.run_once()
    original = await revision_for(factory, first)
    await next_turn(factory, worker, owner, branch, "Future change", "second-request")
    async with factory() as db:
        plan = await regeneration_plan(db, owner, original.id)
        assert plan.boundary.thread_id is None and plan.boundary.turn_id is None
        assert plan.snapshot.base_revision_id is None
        assert plan.snapshot.user_text == "coral wig"


async def test_manual_continuation_keeps_saved_context_after_later_turns(factory, tmp_path):
    worker, runtime, owner, branch, first = await prepared(factory, tmp_path)
    await worker.run_once()
    original = await revision_for(factory, first)
    async with factory.begin() as db:
        manual = await save_manual(db, owner, branch, SaveManualRevision(
            client_request_id="manual-save",base_revision_id=original.id,expected_branch_version=1,
            clean_asset_id=original.clean_asset_id,recipe={"faceWidth":0.2}))
        checkpoint = manual.context_checkpoint.copy()
    await next_turn(factory, worker, owner, branch, "Future blue eyes", "second-request")
    async with factory() as db:
        plan = await continuation_plan(db, owner, branch, manual.id, 3)
        assert plan.boundary.turn_id == checkpoint["turn_id"]
        assert plan.clean_asset_id == manual.clean_asset_id
        assert plan.revision_id == manual.id
        with pytest.raises(ApplicationError,match="original_request_unavailable"):
            await regeneration_plan(db, owner, manual.id)


async def test_incomplete_turn_is_not_a_history_boundary(factory, tmp_path):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path, "crash_after_image")
    await worker.run_once()
    image = await revision_for(factory, opid)
    async with factory() as db:
        with pytest.raises(ApplicationError,match="history_checkpoint_unconfirmed"):
            await continuation_plan(db,owner,branch,image.id,1)
        with pytest.raises(ApplicationError,match="history_checkpoint_unconfirmed"):
            await regeneration_plan(db,owner,image.id)


async def test_history_ownership_and_version_are_checked_before_use(factory, tmp_path):
    worker, runtime, owner, branch, opid = await prepared(factory,tmp_path)
    await worker.run_once()
    image = await revision_for(factory, opid)
    other, _, other_branch = await setup(factory)
    async with factory() as db:
        with pytest.raises(ApplicationError,match="not_found"):
            await regeneration_plan(db,other,image.id)
        with pytest.raises(ApplicationError,match="not_found"):
            await continuation_plan(db,other,other_branch,image.id,0)
        with pytest.raises(ApplicationError,match="state_conflict"):
            await continuation_plan(db,owner,branch,image.id,0)


async def test_missing_original_checkpoint_is_not_inferred_from_latest_epoch(factory, tmp_path):
    worker, runtime, owner, branch, opid = await prepared(factory,tmp_path)
    await worker.run_once()
    image = await revision_for(factory,opid)
    async with factory.begin() as db:
        # Simulates a pre-migration operation. Its original boundary was never recorded.
        (await db.get(Operation,opid)).context_before = None
    async with factory() as db:
        with pytest.raises(ApplicationError,match="history_checkpoint_unavailable"):
            await regeneration_plan(db,owner,image.id)
