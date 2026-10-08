"""Deleting a design is permanent: content goes, money records stay, guards stay for everyone else."""
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.design.models import (Asset, AssetReviewCache, Branch, Design, DomainEvent, InputRecord, LedgerEntry, Message, Operation, Quote,
                               Revision, Wallet)
from app.design.purge import DeleteRequest, purge_design, remove_objects
from app.design.service import ApplicationError, create_design
from .test_operations import factory
from .test_migrations import migration_database
from .test_worker import prepared


async def finished_design(factory, tmp_path):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path)
    assert await worker.run_once() == opid
    async with factory() as db:
        design = (await db.get(Branch, branch)).design_id
        return worker, owner, design, branch, opid


def request(design):
    return DeleteRequest(expected_metadata_version=design.metadata_version)


async def counts(factory, design):
    async with factory() as db:
        branches = select(Branch.id).where(Branch.design_id == design)
        return {
            "designs": await db.scalar(select(func.count()).select_from(Design).where(Design.id == design)),
            "branches": await db.scalar(select(func.count()).select_from(Branch).where(Branch.design_id == design)),
            "revisions": await db.scalar(select(func.count()).select_from(Revision).where(Revision.design_id == design)),
            "operations": await db.scalar(select(func.count()).select_from(Operation).where(Operation.design_id == design)),
            "messages": await db.scalar(select(func.count()).select_from(Message).where(Message.branch_id.in_(branches))),
            "snapshots": await db.scalar(select(func.count()).select_from(InputRecord).where(InputRecord.branch_id.in_(branches))),
            "events": await db.scalar(select(func.count()).select_from(DomainEvent).where(DomainEvent.design_id == design)),
        }


async def test_delete_removes_the_design_and_everything_in_it(factory, tmp_path):
    worker, owner, design_id, branch, opid = await finished_design(factory, tmp_path)
    before = await counts(factory, design_id)
    assert all(value > 0 for value in before.values()), before
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        keys = await purge_design(db, owner, design_id, request(design))
    assert set((await counts(factory, design_id)).values()) == {0}
    assert keys and all(key.startswith("masters/") for key in keys)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Asset).where(Asset.object_key.in_(keys))) == 0


async def test_a_cached_review_verdict_goes_with_its_operation(factory, tmp_path):
    # source_operation_id is NOT NULL, so the purge must delete the cache row rather than clear its link.
    worker, owner, design_id, branch, opid = await finished_design(factory, tmp_path)
    async with factory.begin() as db:
        db.add(AssetReviewCache(owner_id=owner, sha256="a" * 64, policy_key="b" * 64, verdict="allow", source_operation_id=opid))
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        await purge_design(db, owner, design_id, request(design))
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AssetReviewCache).where(AssetReviewCache.owner_id == owner)) == 0


async def test_money_records_survive_and_only_lose_their_operation_link(factory, tmp_path):
    worker, owner, design_id, branch, opid = await finished_design(factory, tmp_path)
    async with factory() as db:
        ledger = [(row.id, row.balance_delta, row.reserved_delta, row.balance_after, row.reserved_after, row.kind, row.idempotency_key)
                  for row in (await db.scalars(select(LedgerEntry).where(LedgerEntry.owner_id == owner).order_by(LedgerEntry.id))).all()]
        wallet = await db.get(Wallet, owner)
        balance = (wallet.balance, wallet.reserved)
        quotes = await db.scalar(select(func.count()).select_from(Quote).where(Quote.owner_id == owner))
        linked = await db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.operation_id == opid))
    assert ledger and linked
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        await purge_design(db, owner, design_id, request(design))
    async with factory() as db:
        after = [(row.id, row.balance_delta, row.reserved_delta, row.balance_after, row.reserved_after, row.kind, row.idempotency_key)
                 for row in (await db.scalars(select(LedgerEntry).where(LedgerEntry.owner_id == owner).order_by(LedgerEntry.id))).all()]
        assert after == ledger
        assert (await db.get(Wallet, owner)).balance == balance[0] and (await db.get(Wallet, owner)).reserved == balance[1]
        assert await db.scalar(select(func.count()).select_from(Quote).where(Quote.owner_id == owner)) == quotes
        assert await db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.operation_id == opid)) == 0


async def test_an_image_still_used_by_another_design_is_kept(factory, tmp_path):
    worker, owner, design_id, branch, opid = await finished_design(factory, tmp_path)
    async with factory.begin() as db:
        other = await create_design(db, owner, "other-design-key", "Other")
        revision = (await db.scalars(select(Revision).where(Revision.design_id == design_id))).first()
        shared = revision.clean_asset_id
        db.add(Revision(design_id=other.id, branch_id=other.last_branch_id, clean_asset_id=shared, root_source_asset_id=shared, source_kind="manual"))
        other_id = other.id
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        keys = await purge_design(db, owner, design_id, request(design))
    async with factory() as db:
        assert await db.get(Asset, shared) is not None
        assert await db.scalar(select(func.count()).select_from(Revision).where(Revision.design_id == other_id)) == 1
        assert not any(shared in key for key in keys)


async def test_guards_still_refuse_every_other_writer(factory, tmp_path):
    worker, owner, design_id, branch, opid = await finished_design(factory, tmp_path)
    for statement in ("DELETE FROM v2_revisions", "UPDATE v2_ledger SET balance_delta = balance_delta + 1",
                      "UPDATE v2_ledger SET operation_id = NULL", "DELETE FROM v2_input_snapshots", "DELETE FROM v2_quotes"):
        async with factory() as db:
            with pytest.raises(DBAPIError):
                await db.execute(text(statement))
    async with factory() as db:
        assert (await counts(factory, design_id))["revisions"] > 0
    # The setting does not leak to the next transaction either.
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        await purge_design(db, owner, design_id, request(design))
    async with factory() as db:
        with pytest.raises(DBAPIError):
            await db.execute(text("UPDATE v2_ledger SET operation_id = NULL"))


async def test_ledger_may_not_change_anything_but_its_link_even_while_purging(factory, tmp_path):
    worker, owner, design_id, branch, opid = await finished_design(factory, tmp_path)
    async with factory() as db:
        async with db.begin():
            await db.execute(text("SELECT set_config('kig.v2_design_purge', 'on', true)"))
            with pytest.raises(DBAPIError):
                await db.execute(text("UPDATE v2_ledger SET balance_delta = balance_delta + 1, operation_id = NULL"))


async def test_a_busy_or_changed_design_is_not_deleted(factory, tmp_path):
    worker, runtime, owner, branch, opid = await prepared(factory, tmp_path)
    async with factory() as db:
        design_id = (await db.get(Branch, branch)).design_id
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        with pytest.raises(ApplicationError, match="design_busy"):
            await purge_design(db, owner, design_id, request(design))
    assert (await counts(factory, design_id))["designs"] == 1
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        with pytest.raises(ApplicationError, match="metadata_conflict"):
            await purge_design(db, owner, design_id, DeleteRequest(expected_metadata_version=design.metadata_version + 1))


async def test_another_account_cannot_delete_it(factory, tmp_path):
    worker, owner, design_id, branch, opid = await finished_design(factory, tmp_path)
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        with pytest.raises(ApplicationError, match="not_found"):
            await purge_design(db, "someone-else", design_id, request(design))
    assert (await counts(factory, design_id))["designs"] == 1


async def test_removed_assets_lose_their_files_after_the_commit(factory, tmp_path, monkeypatch):
    worker, owner, design_id, branch, opid = await finished_design(factory, tmp_path)
    root = Path(worker.settings.v2_storage_dir)
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        keys = await purge_design(db, owner, design_id, request(design))
    assert keys and all((root / key).exists() for key in keys)  # nothing is removed before the commit
    import app.design.purge as module
    monkeypatch.setattr(module, "get_settings", lambda: worker.settings)
    remove_objects(keys)
    assert not any((root / key).exists() for key in keys)
    remove_objects(keys)  # a second run, with the files already gone, is harmless


# ── through the HTTP API ─────────────────────────────────────────────────────────────────

from .test_api import api  # noqa: E402,F401


async def test_http_delete_is_permanent_and_there_is_no_trash(api):
    client, factory, owner, design, branch = api
    found = await client.get(f"/api/v2/designs/{design}")
    assert found.status_code == 200
    version = found.json()["metadata_version"]
    wrong = await client.request("DELETE", f"/api/v2/designs/{design}", json={"expected_metadata_version": version + 5})
    assert wrong.status_code == 409
    extra = await client.request("DELETE", f"/api/v2/designs/{design}", json={"expected_metadata_version": version, "expected_retention_days": 7})
    assert extra.status_code == 422  # the old trash confirmation is gone
    deleted = await client.request("DELETE", f"/api/v2/designs/{design}", json={"expected_metadata_version": version})
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
    assert (await client.get(f"/api/v2/designs/{design}")).status_code == 404
    assert (await client.get(f"/api/v2/designs/{design}/lifecycle")).status_code == 404
    for status in ("active", "archived"):
        listing = await client.get("/api/v2/designs", params={"status": status})
        assert design not in [item["id"] for item in listing.json()["items"]]
    assert (await client.get("/api/v2/designs", params={"status": "trash"})).status_code == 422
    assert (await client.post(f"/api/v2/designs/{design}/restore", json={"expected_metadata_version": version})).status_code in {404, 405}
    again = await client.request("DELETE", f"/api/v2/designs/{design}", json={"expected_metadata_version": version})
    assert again.status_code == 404
