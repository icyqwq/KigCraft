from uuid import uuid4
import pytest
from sqlalchemy import select
from app.design.candidates import candidates
from app.design.models import Branch, Operation, Wallet
from app.design.service import ApplicationError
from .test_migrations import migration_database
from .test_operations import factory, quoted, request, setup, submit_request
from .test_worker import prepared
from .test_history_boundaries import next_turn, revision_for
from .test_api import api


async def retry(factory, worker, owner, branch, revision):
    async with factory() as db:
        version = (await db.get(Branch, branch)).state_version
    payload = request(text="", regenerate_revision_id=revision.id, client_request_id="retry-"+str(uuid4()),
                      expected_branch_version=version)
    op = await submit_request(factory, owner, branch, await quoted(factory, owner, branch, payload))
    await worker.run_once()
    return await revision_for(factory, op)


async def test_group_has_original_and_retries_across_branches_with_stable_pages(factory, tmp_path):
    worker, runtime, owner, branch, first = await prepared(factory, tmp_path)
    await worker.run_once()
    original = await revision_for(factory, first)
    second = await retry(factory, worker, owner, branch, original)
    third = await retry(factory, worker, owner, second.branch_id, second)
    async with factory() as db:
        page = await candidates(db, owner, original.id, limit=1)
        assert [r.id for r in page.items] == [third.id] and page.total == 3
        assert page.group["requested_operation_kind"] == "generate"
        assert (await candidates(db, owner, second.id)).group == page.group
        wallet = (await db.get(Wallet, owner)).balance
    calls = len(runtime.calls)
    # A newly published retry cannot enter an already-open page window.
    fourth = await retry(factory, worker, owner, third.branch_id, third)
    async with factory() as db:
        older = await candidates(db, owner, original.id, cursor=page.next_cursor, limit=1)
        oldest = await candidates(db, owner, original.id, cursor=older.next_cursor, limit=1)
        assert older.total == oldest.total == 3
        assert [r.id for r in older.items + oldest.items] == [second.id, original.id]
        assert oldest.next_cursor is None
        assert [r.id for r in (await candidates(db, owner, original.id)).items] == [fourth.id, third.id, second.id, original.id]
        assert (await db.get(Branch, branch)).current_revision_id == original.id
        assert (await db.get(Wallet, owner)).balance == wallet - 1
        with pytest.raises(ApplicationError, match="invalid_history_cursor"):
            await candidates(db, owner, second.id, cursor=page.next_cursor)
    after_retry_calls = len(runtime.calls)
    assert after_retry_calls > calls
    async with factory() as db:
        await candidates(db, owner, fourth.id)
    assert len(runtime.calls) == after_retry_calls


async def test_changed_input_context_and_manual_images_do_not_join_group(factory, tmp_path):
    from app.contracts.v2 import ContinuationDraft
    from app.design.manual import SaveManualRevision, save_manual
    from .test_message_edit import user_message
    worker, runtime, owner, branch, first = await prepared(factory, tmp_path)
    await worker.run_once(); original = await revision_for(factory, first)
    second = await retry(factory, worker, owner, branch, original)
    msg = await user_message(factory, first)
    payload = request(text="different request", client_request_id="edited-message", expected_branch_version=1,
                      continuation=ContinuationDraft(boundary_message_id=msg.id))
    edit = await submit_request(factory, owner, branch, await quoted(factory, owner, branch, payload))
    await worker.run_once(); edited = await revision_for(factory, edit)
    async with factory.begin() as db:
        manual = await save_manual(db, owner, branch, SaveManualRevision(
            client_request_id="manual-one", base_revision_id=original.id, expected_branch_version=1,
            clean_asset_id=original.clean_asset_id, recipe={"faceWidth":0.2}))
        assert (await candidates(db, owner, manual.id)).group is None
        assert (await candidates(db, owner, original.id)).total == 2
        assert (await candidates(db, owner, edited.id)).total == 1
        assert (await candidates(db, owner, edited.id)).group != (await candidates(db, owner, original.id)).group
        # Simulate a legacy record without a reliable pre-turn boundary.
        (await db.get(Operation, second.operation_id)).context_before = None
    async with factory() as db:
        assert (await candidates(db, owner, original.id)).total == 1
        unknown = await candidates(db, owner, second.id)
        assert unknown.group is None and [r.id for r in unknown.items] == [second.id]
    foreign, _, _ = await setup(factory)
    async with factory() as db:
        with pytest.raises(ApplicationError, match="not_found"):
            await candidates(db, foreign, original.id)


async def test_http_candidates_private_read_only_and_cross_owner_rejected(api, tmp_path):
    from app.operations.worker import LocalWorker
    from app.core.config import get_settings
    from .test_worker import ScriptedRuntime
    client, factory, owner, design, branch = api
    op = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    worker = LocalWorker(factory, get_settings().model_copy(update={"v2_runtime_dir":str(tmp_path/"runtime")}), runtime=ScriptedRuntime())
    await worker.run_once(); original = await revision_for(factory, op)
    other = await retry(factory, worker, owner, branch, original)
    before = (await client.get(f"/api/v2/designs/{design}")).json()
    quota = (await client.get("/api/v2/account/quota")).json()
    calls = len(worker.runtime.calls)
    path = f"/api/v2/revisions/{original.id}/candidates"
    response = await client.get(path+"?limit=1")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["items"][0]["id"] == other.id and body["total"] == 2 and body["next_cursor"]
    assert "epoch" not in repr(body) and "thread" not in repr(body) and "input_snapshot_id" not in repr(body)
    assert (await client.get(path+"?cursor=garbage")).status_code == 400
    assert (await client.get(path+"?limit=101")).status_code == 422
    assert (await client.get("/api/v2/revisions/missing/candidates")).status_code == 404
    assert (await client.get(f"/api/v2/designs/{design}")).json() == before
    assert (await client.get("/api/v2/account/quota")).json() == quota
    assert len(worker.runtime.calls) == calls
    # Same auth cookie cannot read another owner's generated image.
    foreign, _, foreign_branch = await setup(factory)
    foreign_op = await submit_request(factory, foreign, foreign_branch, await quoted(factory, foreign, foreign_branch))
    foreign_worker = LocalWorker(factory, get_settings().model_copy(update={"v2_local_user_id":foreign,"v2_runtime_dir":str(tmp_path/"foreign-runtime")}), runtime=worker.runtime)
    await foreign_worker.run_once(); foreign_image = await revision_for(factory, foreign_op)
    assert (await client.get(f"/api/v2/revisions/{foreign_image.id}/candidates")).status_code == 404
