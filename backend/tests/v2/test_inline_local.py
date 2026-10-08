"""The editor's own local generation: a bare result asset, no message, no revision, no version, no change to the conversation."""
import pytest
from uuid import NAMESPACE_URL, uuid4, uuid5
from PIL import Image
from sqlalchemy import func, select
from app.assets.storage import upload_asset
from app.design.manual import SaveManualRevision, save_manual
from app.design.models import Asset, Branch, Design, Message, Operation, Revision, Wallet
from app.design.service import ApplicationError
from app.operations.service import quote, submit
from .test_local_edit import accepted, draft, local_settings  # noqa: F401
from .test_local_images import png
from .test_migrations import migration_database  # noqa: F401
from .test_operations import factory  # noqa: F401


def inline(request):
    return request.model_copy(update={"inline_local": True})


async def accepted_inline(factory, tmp_path, monkeypatch, **kwargs):
    import sys
    from .test_local_edit import ScriptedRuntime, LocalWorker, Settings, base64, quote as _quote, submit as _submit
    owner, branch, base, request, output = await draft(factory, tmp_path, kwargs.get("mode", "preserve"))
    request = inline(request)
    output = kwargs.get("image", output)
    monkeypatch.setattr(sys.modules[ScriptedRuntime.__module__], "png", lambda: base64.b64encode(png(output)).decode())
    async with factory.begin() as db:
        price = await _quote(db, owner, branch, request)
        op = await _submit(db, owner, branch, request.model_copy(update={"quote_id": price.id}))
        opid = op.id
    settings = Settings(_env_file=None, app_env="test", v2_local_edit_enabled=True, v2_local_user_id=owner,
                        v2_storage_dir=str(tmp_path / "assets"), v2_runtime_dir=str(tmp_path / "runtime"))
    worker = LocalWorker(factory, settings, runtime=ScriptedRuntime("image"))
    return worker, owner, branch, base, opid, request


async def test_result_is_a_bare_asset_and_the_conversation_is_untouched(factory, tmp_path, monkeypatch):
    worker, owner, branch, base, opid, _ = await accepted_inline(factory, tmp_path, monkeypatch)
    async with factory() as db:
        before = (await db.get(Branch, branch)).state_version, (await db.get(Design, (await db.get(Branch, branch)).design_id)).last_branch_id
        messages = await db.scalar(select(func.count()).select_from(Message).where(Message.branch_id == branch))
        operation = await db.get(Operation, opid)
        assert operation.branch_id != branch  # a throw-away branch, forked from the edited image
    await worker.run_once()
    async with factory() as db:
        operation = await db.get(Operation, opid)
        assert (operation.kind, operation.state, operation.settled_units) == ("local_edit", "succeeded", 1)
        assert (await db.scalar(select(func.count()).select_from(Revision).where(Revision.operation_id == opid))) == 0
        asset = await db.get(Asset, str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{opid}:main_image")))
        assert asset is not None and asset.owner_id == owner and asset.kind == "master" and (asset.width, asset.height) == (160, 160)
        main = await db.get(Branch, branch)
        assert (main.state_version, (await db.get(Design, main.design_id)).last_branch_id) == before
        assert main.current_revision_id == base.id
        # The model's reply stays in the throw-away branch; the user's conversation gains nothing.
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.branch_id == branch)) == messages
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (4, 0)


async def test_a_newer_branch_version_does_not_make_the_edit_stale(factory, tmp_path, monkeypatch):
    owner, branch, base, request, _ = await draft(factory, tmp_path, "preserve")
    async with factory.begin() as db:
        (await db.get(Branch, branch)).state_version = 7  # the conversation moved on since the editor opened
    async with factory.begin() as db:
        price = await quote(db, owner, branch, inline(request))
        operation = await submit(db, owner, branch, inline(request).model_copy(update={"quote_id": price.id}))
        assert operation.state == "queued"


async def test_a_result_with_the_bases_proportions_is_scaled_to_its_size(factory, tmp_path, monkeypatch):
    worker, owner, branch, base, opid, _ = await accepted_inline(factory, tmp_path, monkeypatch, image=Image.new("RGB", (320, 320), "green"))
    await worker.run_once()
    async with factory() as db:
        assert (await db.get(Operation, opid)).state == "succeeded"
        asset = await db.get(Asset, str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{opid}:main_image")))
        assert (asset.width, asset.height) == (160, 160)


async def test_a_result_with_other_proportions_is_refused_and_not_charged(factory, tmp_path, monkeypatch):
    worker, owner, branch, base, opid, _ = await accepted_inline(factory, tmp_path, monkeypatch, image=Image.new("RGB", (320, 160), "green"))
    await worker.run_once()
    async with factory() as db:
        operation = await db.get(Operation, opid)
        assert (operation.state, operation.error_code) == ("failed", "local_output_size_mismatch")
        assert await db.get(Asset, str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{opid}:main_image"))) is None
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (5, 0)


async def test_saving_from_the_editor_starts_the_revision_at_the_adopted_result(factory, tmp_path, monkeypatch):
    worker, owner, branch, base, opid, _ = await accepted_inline(factory, tmp_path, monkeypatch)
    await worker.run_once()
    result_id = str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{opid}:main_image"))
    recipe = {"imageCoordinates": {"version": 1, "width": 160, "height": 160, "effectScale": 1}}
    async with factory.begin() as db:
        rendered = upload_asset(png(Image.new("RGB", (160, 160), "red")), owner, tmp_path / "assets")
        stranger = upload_asset(png(Image.new("RGB", (160, 160), "blue")), owner, tmp_path / "assets")
        db.add_all([rendered, stranger]); await db.flush()
        rendered_id, stranger_id = rendered.id, stranger.id
        version = (await db.get(Branch, branch)).state_version
    base_request = dict(client_request_id=str(uuid4()), base_revision_id=base.id, expected_branch_version=version,
                        clean_asset_id=rendered_id, recipe=recipe)
    async with factory.begin() as db:
        with pytest.raises(ApplicationError, match="not_found"):  # an ordinary upload is not an adopted result
            await save_manual(db, owner, branch, SaveManualRevision(**{**base_request, "client_request_id": str(uuid4()), "root_asset_id": stranger_id}))
    async with factory.begin() as db:
        revision = await save_manual(db, owner, branch, SaveManualRevision(**base_request, root_asset_id=result_id))
        assert revision.root_source_asset_id == result_id and revision.number > base.number


async def test_the_editor_document_may_start_at_an_adopted_result(factory, tmp_path, monkeypatch):
    from app.design.editor_save import SaveEditorDocument, save_editor
    from .test_editor_save import document
    worker, owner, branch, base, opid, _ = await accepted_inline(factory, tmp_path, monkeypatch)
    await worker.run_once()
    result_id = str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{opid}:main_image"))
    async with factory.begin() as db:
        rendered = upload_asset(png(Image.new("RGB", (160, 160), "red")), owner, tmp_path / "assets")
        stranger = upload_asset(png(Image.new("RGB", (160, 160), "blue")), owner, tmp_path / "assets")
        db.add_all([rendered, stranger]); await db.flush()
        adopted = await db.get(Asset, result_id)
        revision = await db.get(Revision, base.id)
        version = (await db.get(Branch, branch)).state_version
        request = dict(client_request_id=str(uuid4()), expected_branch_version=version, clean_asset_id=rendered.id)
        good = SaveEditorDocument(**request, document=document(revision, adopted))
        bad = SaveEditorDocument(**{**request, "client_request_id": str(uuid4())}, document=document(revision, stranger))
    async with factory.begin() as db:
        with pytest.raises(ApplicationError, match="editor_source_mismatch"):
            await save_editor(db, owner, branch, bad, storage_root=tmp_path / "assets")
    async with factory.begin() as db:
        kind, checkpoint, saved = await save_editor(db, owner, branch, good, storage_root=tmp_path / "assets")
        assert kind == "revision" and saved.root_source_asset_id == result_id and checkpoint.root_asset_id == result_id


async def test_the_editor_can_read_the_throwaway_branch_it_polls(factory, tmp_path, monkeypatch):
    from app.design.router import history_json, operation_json
    worker, owner, branch, base, opid, _ = await accepted_inline(factory, tmp_path, monkeypatch)
    await worker.run_once()
    async with factory() as db:
        operation = await db.get(Operation, opid)
        scratch = await db.get(Branch, operation.branch_id)
        design = await db.get(Design, scratch.design_id)
        assert scratch.id != branch and design.last_branch_id == branch
        page = await history_json(db, design, scratch)
        assert not any(item["type"] == "revision" and item["revision"]["operation_id"] == opid for item in page["items"])
        summary = operation_json(operation)
        assert summary["state"] == "succeeded" and summary["result_asset_id"] == str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{opid}:main_image"))
