from sqlalchemy import select
import pytest
from app.design.manual import SaveManualRevision, save_manual
from app.design.models import Asset, Branch, Revision, Wallet
from app.design.service import ApplicationError
from app.operations.publish import publish
from app.operations.lifecycle import finish
from .test_operations import factory, setup, quoted, submit_request, running, staged
from .test_migrations import migration_database

async def base(factory, tmp_path):
    owner, _, branch = await setup(factory)
    opid = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    token = await running(factory, opid)
    async with factory.begin() as db:
        revision = await publish(db, opid, "worker", token, "main_image", staged(tmp_path),
                                 storage_root=tmp_path, finalize=False)
        # This fixture represents a completed generation, not an output whose
        # upstream turn is still unknown. Use the worker's terminal transition.
        await finish(db, opid, "worker", token, upstream_status="completed")
    return owner, branch, revision

async def test_manual_save_is_idempotent_free_and_keeps_original_root(factory, tmp_path):
    owner, branch_id, original = await base(factory, tmp_path)
    payload = SaveManualRevision(client_request_id="manual-first", base_revision_id=original.id,
        expected_branch_version=1, clean_asset_id=original.clean_asset_id, recipe={"version":1,"faceWidth":0.2})
    async with factory.begin() as db:
        first = await save_manual(db, owner, branch_id, payload)
    async with factory.begin() as db:
        replay = await save_manual(db, owner, branch_id, payload)
        assert replay.id == first.id
        assert original.number == 1 and first.number == replay.number == 2
        second = await save_manual(db, owner, branch_id, payload.model_copy(update={
            "client_request_id":"manual-second", "base_revision_id":first.id,
            "expected_branch_version":2, "recipe":{"version":1,"faceWidth":0.4}}))
        assert second.number == 3
        assert second.root_source_asset_id == original.root_source_asset_id
        assert second.parent_revision_id == first.id
        assert (await db.get(Wallet, owner)).balance == 4
        assert (await db.get(Branch, branch_id)).state_version == 3

async def test_manual_save_rejects_cross_owner_asset(factory, tmp_path):
    owner, branch_id, original = await base(factory, tmp_path)
    other, _, _ = await setup(factory)
    async with factory.begin() as db:
        asset = Asset(owner_id=other,object_key="foreign.png",sha256="a"*64,mime_type="image/png",
                      kind="reference",width=16,height=16,byte_size=1,status="ready")
        db.add(asset)
        await db.flush()
        asset_id=asset.id
    with pytest.raises(ApplicationError,match="not_found"):
        async with factory.begin() as db:
            await save_manual(db,owner,branch_id,SaveManualRevision(client_request_id="manual-cross",
                base_revision_id=original.id,expected_branch_version=1,clean_asset_id=asset_id,recipe={}))

async def test_manual_stale_save_preserves_current_base(factory, tmp_path):
    owner, branch_id, original = await base(factory, tmp_path)
    with pytest.raises(ApplicationError,match="state_conflict"):
        async with factory.begin() as db:
            await save_manual(db,owner,branch_id,SaveManualRevision(client_request_id="manual-stale",
                base_revision_id=original.id,expected_branch_version=0,
                clean_asset_id=original.clean_asset_id,recipe={}))
    async with factory() as db:
        assert (await db.get(Branch,branch_id)).current_revision_id == original.id


async def test_image_coordinate_recipe_is_bound_to_root_and_export_dimensions(factory, tmp_path):
    owner, branch_id, original = await base(factory, tmp_path)
    async with factory() as db:
        root = await db.get(Asset, original.root_source_asset_id)
        width, height = root.width, root.height
    recipe = {"imageCoordinates": {"version": 1, "width": width, "height": height, "effectScale": 2.0}}
    payload = SaveManualRevision(client_request_id="manual-coordinates", base_revision_id=original.id,
        expected_branch_version=1, clean_asset_id=original.clean_asset_id, recipe=recipe)
    async with factory.begin() as db:
        saved = await save_manual(db, owner, branch_id, payload)
        assert saved.recipe == recipe
    async with factory.begin() as db:
        small = Asset(owner_id=owner, object_key="smaller.png", sha256="b"*64, mime_type="image/png",
            kind="reference", width=width+1, height=height, byte_size=1, status="ready")
        db.add(small)
        await db.flush()
        small_id = small.id
    with pytest.raises(ApplicationError, match="recipe_image_mismatch"):
        async with factory.begin() as db:
            await save_manual(db, owner, branch_id, payload.model_copy(update={
                "client_request_id": "wrong-export-size", "base_revision_id": saved.id,
                "expected_branch_version": 2, "clean_asset_id": small_id}))
    async with factory() as db:
        assert (await db.get(Branch, branch_id)).current_revision_id == saved.id


@pytest.mark.parametrize("coordinates,code", [
    (None, "invalid_recipe"),
    ({"version":2,"width":16,"height":16,"effectScale":1}, "invalid_recipe"),
    ({"version":1,"width":"16","height":16,"effectScale":1}, "invalid_recipe"),
    ({"version":1,"width":16,"height":16,"effectScale":0}, "invalid_recipe"),
    ({"version":1,"width":1024,"height":512,"effectScale":1}, "recipe_image_mismatch"),
])
async def test_invalid_image_coordinate_recipe_cannot_advance_branch(factory, tmp_path, coordinates, code):
    owner, branch_id, original = await base(factory, tmp_path)
    with pytest.raises(ApplicationError, match=code):
        async with factory.begin() as db:
            await save_manual(db, owner, branch_id, SaveManualRevision(client_request_id="bad-coordinates",
                base_revision_id=original.id, expected_branch_version=1, clean_asset_id=original.clean_asset_id,
                recipe={"imageCoordinates":coordinates}))
    async with factory() as db:
        assert (await db.get(Branch, branch_id)).current_revision_id == original.id
