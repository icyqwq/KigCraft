"""Component checks for draft content deletion; no browser or model."""
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from app.assets.storage import upload_in_transaction, normalize_upload
from app.auth.models import UserRole
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import User
from app.design.drafts import SaveDraft, DeleteDraft, save_draft, delete_draft
from app.design.models import EditorDraft
from app.design.service import ApplicationError
from app.maintenance.deleted_drafts import inspect_drafts, scrub_drafts
from app.maintenance.orphan_assets import CleanupError
from app.maintenance.upload_retention import inspect_uploads
from .test_drafts import fixture, request
from .test_legacy_inventory import fingerprints
from .test_migrations import migration_database
from .test_operations import factory
from .test_orphan_assets import png


async def prepared(factory, tmp_path, copies=1):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    async with factory.begin() as db:
        (await db.get(User, owner)).role = UserRole.admin
        attachment = await upload_in_transaction(db, png(), owner, tmp_path)
        attachment.created_at = utc_now() - timedelta(days=60)
        db.add(attachment)
    payload = request(branch, revision, root).model_dump(mode="json")
    payload["document"]["content"]["localEditNote"] = "private cleanup test note"
    payload["document"]["attachments"] = [{"slot": "extra", "asset_id": attachment.id,
        "file_name": "private-reference.png", "last_modified": 7}]
    keys = [str(uuid4()) for _ in range(copies)]
    async with factory.begin() as db:
        for key in keys:
            await save_draft(db, owner, key, SaveDraft.model_validate(payload))
    return owner, keys, payload, attachment


async def remove(factory, owner, key):
    deletion = DeleteDraft(client_request_id=str(uuid4()), expected_version=1)
    async with factory.begin() as db:
        result = await delete_draft(db, owner, key, deletion)
    return deletion, result


async def legacy_deleted(factory, tmp_path, copies=1):
    owner, keys, payload, asset = await prepared(factory, tmp_path, copies)
    for key in keys:
        await remove(factory, owner, key)
    # Simulate content retained by the previous soft-delete implementation.
    async with factory.begin() as db:
        for key in keys:
            row = await db.get(EditorDraft, key)
            row.document = payload["document"]
            row.device_id = payload["device_id"]
    return owner, keys, payload, asset


async def upload_candidates(factory, root):
    async with factory.begin() as db:
        report = await inspect_uploads(db, root, Settings(_env_file=None,
            v2_upload_retention_days=30), "expire")
    return {row["asset_id"] for row in report["candidates"]}


async def test_delete_clears_private_content_without_resurrection_or_shared_file_removal(factory, tmp_path):
    owner, keys, payload, attachment = await prepared(factory, tmp_path, 2)
    assert attachment.id not in await upload_candidates(factory, tmp_path)
    before = await fingerprints(factory)
    deletion, result = await remove(factory, owner, keys[0])
    async with factory.begin() as db:
        row = await db.get(EditorDraft, keys[0])
        assert row.document == {} and row.device_id == "" and row.version == 2
        assert await delete_draft(db, owner, keys[0], deletion) == result
        assert (await save_draft(db, owner, keys[0], SaveDraft.model_validate(payload)))["version"] == 1
        assert row.document == {} and row.deleted_at is not None
        assert (await db.get(EditorDraft, keys[1])).document["content"]["localEditNote"] == "private cleanup test note"
    after = await fingerprints(factory)
    excluded = {"v2_editor_drafts", "v2_draft_write_receipts"}
    assert {k:v for k,v in before.items() if k not in excluded} == {k:v for k,v in after.items() if k not in excluded}
    assert attachment.id not in await upload_candidates(factory, tmp_path)
    await remove(factory, owner, keys[1])
    assert attachment.id in await upload_candidates(factory, tmp_path)
    assert (tmp_path / attachment.object_key).read_bytes() == normalize_upload(png())[0]
    with pytest.raises(ApplicationError, match="draft_conflict"):
        async with factory.begin() as db:
            await save_draft(db, owner, keys[0], SaveDraft.model_validate({
                **payload, "client_request_id": str(uuid4()), "expected_version": 2}))


async def test_scrub_old_deleted_contents_keeps_receipts_and_business_rows(factory, tmp_path):
    owner, keys, payload, attachment = await legacy_deleted(factory, tmp_path)
    before = await fingerprints(factory)
    async with factory.begin() as db:
        report = await inspect_drafts(db)
    encoded = json.dumps(report)
    assert "private cleanup test note" not in encoded and payload["device_id"] not in encoded
    async with factory.begin() as db:
        result = await scrub_drafts(db, report["digest"], owner, "User deleted draft contents")
    async with factory.begin() as db:
        assert await scrub_drafts(db, report["digest"], owner, "User deleted draft contents") == result
        row = await db.get(EditorDraft, keys[0])
        assert row.document == {} and row.device_id == "" and row.version == 2
        assert (await save_draft(db, owner, keys[0], SaveDraft.model_validate(payload)))["version"] == 1
    after = await fingerprints(factory)
    excluded = {"v2_editor_drafts", "v2_admin_actions"}
    assert {k:v for k,v in before.items() if k not in excluded} == {k:v for k,v in after.items() if k not in excluded}
    assert attachment.id in await upload_candidates(factory, tmp_path)
    assert (tmp_path / attachment.object_key).exists()


@pytest.mark.parametrize("change", ["content", "restored"])
async def test_changed_or_restored_draft_invalidates_review(factory, tmp_path, change):
    owner, keys, payload, _ = await legacy_deleted(factory, tmp_path)
    async with factory.begin() as db:
        report = await inspect_drafts(db)
    async with factory.begin() as db:
        row = await db.get(EditorDraft, keys[0])
        if change == "content":
            row.document = {"changed": True}
        else:
            row.deleted_at = None
    before = await fingerprints(factory)
    with pytest.raises(CleanupError, match="inventory_changed"):
        async with factory.begin() as db:
            await scrub_drafts(db, report["digest"], owner, "User deleted draft contents")
    assert before == await fingerprints(factory)


async def test_admin_reason_and_transaction_rollback(factory, tmp_path):
    owner, keys, _, _ = await legacy_deleted(factory, tmp_path)
    async with factory.begin() as db:
        report = await inspect_drafts(db)
    before = await fingerprints(factory)
    for actor, reason, code in [(str(uuid4()), "Cleanup", "admin_required"), (owner, "", "reason_required")]:
        with pytest.raises(CleanupError, match=code):
            async with factory.begin() as db:
                await scrub_drafts(db, report["digest"], actor, reason)
    with pytest.raises(RuntimeError, match="rollback"):
        async with factory.begin() as db:
            await scrub_drafts(db, report["digest"], owner, "User deleted draft contents")
            raise RuntimeError("rollback")
    assert await fingerprints(factory) == before


async def test_pagination_live_draft_exclusion_and_payload_bound(factory, tmp_path, monkeypatch):
    from app.maintenance import deleted_drafts
    owner, keys, _, _ = await legacy_deleted(factory, tmp_path, 3)
    async with factory.begin() as db:
        row = await db.get(EditorDraft, keys[0])
        row.deleted_at = None
    async with factory.begin() as db:
        first = await inspect_drafts(db, limit=1)
    async with factory.begin() as db:
        second = await inspect_drafts(db, after=first["next_after"], limit=1)
    assert {first["candidates"][0]["draft_id"], second["candidates"][0]["draft_id"]} == set(keys[1:])
    assert second["next_after"] is None
    monkeypatch.setattr(deleted_drafts, "MAX_CONTENT_BYTES", 1)
    with pytest.raises(CleanupError, match="draft_batch_too_large"):
        async with factory.begin() as db:
            await inspect_drafts(db)
    for options in ({"after": "../invalid"}, {"limit": True}, {"limit": 101}):
        with pytest.raises(CleanupError, match="invalid_retention"):
            async with factory.begin() as db:
                await inspect_drafts(db, **options)
