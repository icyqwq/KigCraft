from uuid import uuid4
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.core.time import utc_now
from app.design.models import EditorDraft, Design, Wallet
from app.design.drafts import save_draft, delete_draft, DeleteDraft
from app.design.draft_catalog import list_account_drafts, read_cursor
from app.design.service import ApplicationError
from tests.v2.test_migrations import migration_database
from tests.v2.test_operations import factory
from tests.v2.test_drafts import fixture, request
from tests.v2.test_api import api

async def test_catalog_is_owner_scoped_bounded_and_stable_when_autosaving(factory, tmp_path):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    other, _, _, _ = await fixture(factory, tmp_path)
    ids = sorted(str(uuid4()) for _ in range(3))
    async with factory.begin() as db:
        for key in ids:
            await save_draft(db, owner, key, request(branch, revision, root))
    async with factory() as db:
        before = (await db.get(Wallet, owner)).balance
        first = await list_account_drafts(db, owner, limit=1)
        assert [r["id"] for r in first["items"]] == [ids[-1]]
        assert not {"document","device_id","root_asset_id"} & first["items"][0].keys()
        assert first["items"][0]["note"] == "local instruction"
        assert (await list_account_drafts(db, other))["items"] == []
        with pytest.raises(ApplicationError, match="invalid_draft_cursor"):
            await list_account_drafts(db, other, cursor=first["next_cursor"])
    async with factory.begin() as db:
        row = await db.get(EditorDraft, ids[0])
        row.updated_at = utc_now() + timedelta(days=1)
    async with factory() as db:
        second = await list_account_drafts(db, owner, cursor=first["next_cursor"], limit=1)
        third = await list_account_drafts(db, owner, cursor=second["next_cursor"], limit=1)
        assert [second["items"][0]["id"], third["items"][0]["id"]] == ids[-2::-1]
        assert third["next_cursor"] is None
        assert (await db.get(Wallet, owner)).balance == before

async def test_catalog_hides_deleted_drafts_and_trashed_designs_but_marks_archives(factory, tmp_path):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    keep, remove = str(uuid4()), str(uuid4())
    async with factory.begin() as db:
        for key in [keep, remove]:
            await save_draft(db, owner, key, request(branch, revision, root))
        await delete_draft(db, owner, remove, DeleteDraft(client_request_id=str(uuid4()), expected_version=1))
        (await db.get(Design, revision.design_id)).archived_at = utc_now()
    async with factory() as db:
        rows = (await list_account_drafts(db, owner))["items"]
        assert len(rows) == 1 and rows[0]["id"] == keep and rows[0]["archived"]
    async with factory.begin() as db:
        (await db.get(Design, revision.design_id)).deleted_at = utc_now()
    async with factory() as db:
        assert (await list_account_drafts(db, owner))["items"] == []
        assert (await db.get(EditorDraft, keep)).deleted_at is None

async def test_catalog_http_requires_login_and_is_private(api):
    client, _, _, _, _ = api
    result = await client.get("/api/v2/account/editor-drafts")
    assert result.status_code == 200 and result.json() == {"items": [], "next_cursor": None}
    assert result.headers["cache-control"] == "private, no-store"
    assert (await client.get("/api/v2/account/editor-drafts?limit=101")).status_code == 422
    assert (await client.get("/api/v2/account/editor-drafts?cursor=bad")).status_code == 400
    client.cookies.clear()
    assert (await client.get("/api/v2/account/editor-drafts")).status_code == 401

@pytest.mark.parametrize("cursor", ["!", "e30", "W10", "bnVsbA", "a"*513])
def test_malformed_cursor_is_rejected(cursor):
    with pytest.raises(ApplicationError, match="invalid_draft_cursor"):
        read_cursor(cursor, "owner")
