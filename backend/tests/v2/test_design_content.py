"""Expired content retirement in disposable PostgreSQL only; no file cleanup."""
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.assets.storage import upload_asset
from app.core.time import utc_now
from app.db.schema_catalog import read_catalog
from app.design.models import (Asset, Branch, Design, DomainEvent, EditorCheckpoint, EditorDraft,
    InputRecord, LegacyImport, Message, Operation, Revision, ViewSet, LedgerEntry, Wallet)
from app.design.catalog import list_designs
from app.design.service import ApplicationError, create_design, owned_design
from app.maintenance import design_content as module, trash_assets
from app.maintenance.orphan_assets import CleanupError
from app.maintenance.backup_bundle import thread_references
from .test_trash_assets import prepared, shared_design
from .test_operations import factory, staged
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints

PRIVATE = "private-expired-content-fixture"
REASON = "Apply approved expired content policy"


async def setup_content(factory, tmp_path):
    owner, revision, asset, cfg, now = await prepared(factory, tmp_path)
    cfg.v2_design_content_cleanup_enabled = True
    thread = str(uuid4())
    async with factory.begin() as db:
        operation = await db.get(Operation, revision.operation_id)
        operation.upstream_thread_id = str(uuid4())
        image = staged(Path(cfg.v2_storage_dir))
        operation.staged_outputs = {"main_image": {"filename": image.path.name, "delivery_key": image.delivery_key, "sha256": image.sha256}}
        operation.context_before = {"prompt": PRIVATE, "fork": {"thread_id": thread}}
        (await db.get(Design, revision.design_id)).title = PRIVATE
        (await db.get(Branch, revision.branch_id)).context_seed = {"user_text": PRIVATE}
        extra = upload_asset((Path(cfg.v2_storage_dir)/asset.object_key).read_bytes(), owner, Path(cfg.v2_storage_dir))
        db.add(extra); await db.flush()
        snapshot = InputRecord(branch_id=revision.branch_id, digest="a"*64,
            snapshot={"user_text": PRIVATE, "asset_id": extra.id, "fork": {"thread_id": thread}})
        db.add(snapshot)
        new_revision = Revision(design_id=revision.design_id, branch_id=revision.branch_id,
            clean_asset_id=asset.id, root_source_asset_id=asset.id, source_kind="manual",
            recipe={"annotations": PRIVATE}, context_checkpoint={"thread_id": thread, "text": PRIVATE})
        db.add(new_revision); await db.flush()
        checkpoint = EditorCheckpoint(owner_id=owner, design_id=revision.design_id, branch_id=revision.branch_id,
            revision_id=new_revision.id, root_asset_id=asset.id, clean_asset_id=asset.id,
            version=1, document={"text": PRIVATE}, document_digest="a"*64)
        db.add(checkpoint)
        db.add(EditorDraft(id=str(uuid4()), owner_id=owner, design_id=revision.design_id,
            branch_id=revision.branch_id, base_revision_id=revision.id, root_asset_id=asset.id,
            version=1, document={"text": PRIVATE}, device_id=PRIVATE))
        db.add(Message(branch_id=revision.branch_id, role="user", content_parts=[{"type":"text","text":PRIVATE}]))
        db.add(LegacyImport(owner_id=owner, source_kind="job", source_id=str(uuid4()), source_digest="b"*64,
            source_summary={"prompt":PRIVATE}, group_key="c"*64, design_id=revision.design_id))
        db.add(ViewSet(owner_id=owner, design_id=revision.design_id, branch_id=revision.branch_id,
            source_revision_id=revision.id, client_request_id=str(uuid4()), request_digest="d"*64,
            snapshot={"user_text":PRIVATE}, snapshot_digest="e"*64, quote_id=operation.quote_id,
            max_units=1, settled_units=0, held_units=0, run_number=1,
            authorization_expires_at=now-timedelta(days=5), state="failed"))
        await db.flush()
        return owner, revision.design_id, revision, extra.id, snapshot.id, checkpoint.id, thread, cfg, now


async def inspect(factory, cfg, now):
    async with factory.begin() as db:
        return await module.inspect_designs(db, cfg, writers_stopped=True, now=now)


async def apply(factory, cfg, now, owner, report):
    async with factory.begin() as db:
        return await module.scrub_designs(db, cfg, report["digest"], owner, REASON, writers_stopped=True, now=now)


async def test_content_removed_but_finance_assets_receipts_and_references_preserved(factory, tmp_path):
    owner, design_id, revision, extra, _, _, thread, cfg, now = await setup_content(factory, tmp_path)
    before = await fingerprints(factory)
    async with factory() as db: schema_before = await read_catalog(db)
    report = await inspect(factory, cfg, now)
    assert len(report["candidates"]) == 1 and PRIVATE not in str(report)
    assert await fingerprints(factory) == before
    resources = report["candidates"][0]["retained_resources"]
    assert extra in resources["asset_ids"] and thread in thread_references(resources)
    result = await apply(factory, cfg, now, owner, report)
    assert result["count"] == 1
    assert await apply(factory, cfg, now, owner, report) == result
    async with factory() as db:
        design = await db.get(Design, design_id)
        assert design.title == "" and design.content_purged_at == now and design.retained_resources == resources
        records, encoded, _ = await module.content_inventory(db, design_id)
        assert PRIVATE not in encoded
        assert (await db.get(Operation, revision.operation_id)).staged_outputs is not None
        assert await read_catalog(db) == schema_before
        for status in ("active", "archived"):
            assert design_id not in [row.id for row in (await list_designs(db, owner, status=status))[0]]
        assert not (await list_designs(db, owner, status="trash"))[0]
        with pytest.raises(ApplicationError, match="not_found"): await owned_design(db, owner, design_id)
    after = await fingerprints(factory)
    for table in ("v2_ledger", "v2_wallets", "v2_quotes", "v2_assets", "v2_draft_write_receipts", "v2_editor_save_receipts"):
        assert before[table] == after[table]
    # Attachments found only in the removed snapshot still belong to expired trash.
    async with factory.begin() as db:
        asset_report = await trash_assets.inspect_assets(db, cfg, writers_stopped=True, now=now)
        assert extra in {row["asset_id"] for row in asset_report["candidates"]}
    assert (Path(cfg.v2_storage_dir)/f"uploads/{extra}.jpg").exists()


async def test_shared_asset_does_not_prevent_scrub_or_get_deleted(factory, tmp_path):
    owner, design_id, revision, _, _, _, _, cfg, now = await setup_content(factory, tmp_path)
    async with factory() as db: asset = await db.get(Asset, revision.clean_asset_id)
    other = await shared_design(factory, owner, asset, now, "active")
    report = await inspect(factory, cfg, now)
    assert len(report["candidates"]) == 1
    await apply(factory, cfg, now, owner, report)
    async with factory.begin() as db:
        assert (await db.get(Design, other)).title == "Other design"
        assert await owned_design(db, owner, other)
        assets = await trash_assets.inspect_assets(db, cfg, writers_stopped=True, now=now)
        assert asset.id not in {item["asset_id"] for item in assets["candidates"]}
    assert (Path(cfg.v2_storage_dir)/asset.object_key).exists()


@pytest.mark.parametrize("kind", ["json", "snapshot_fk"])
async def test_external_content_reference_retains_entire_design(factory, tmp_path, kind):
    owner, design_id, revision, _, snapshot, _, _, cfg, now = await setup_content(factory, tmp_path)
    async with factory.begin() as db:
        other = await create_design(db, owner, str(uuid4()), "Active")
        if kind == "json":
            (await db.get(Branch, other.last_branch_id)).context_seed = {"future_reference": revision.id}
        else:
            # A future/imported snapshot consumer with a different owning design.
            source = await db.get(Operation, revision.operation_id)
            db.add(Operation(owner_id=owner, design_id=other.id, branch_id=other.last_branch_id,
                client_request_id=str(uuid4()), request_digest="e"*64, input_snapshot_id=snapshot,
                kind="turn", state="failed", expected_branch_version=0, reservation_closed=True))
    report = await inspect(factory, cfg, now)
    assert report["candidates"] == []
    assert report["retained"] == [{"design_id":design_id,"reason":"shared_or_unknown_content_reference"}]


@pytest.mark.parametrize("fault", ["disabled", "not_paused", "unknown_operation", "viewset_held", "missing_ack"])
async def test_unapproved_or_unconfirmed_state_is_never_scrubbed(factory, tmp_path, fault):
    owner, design_id, revision, _, _, _, _, cfg, now = await setup_content(factory, tmp_path)
    async with factory.begin() as db:
        if fault == "disabled": cfg.v2_design_content_cleanup_enabled = False
        elif fault == "not_paused":
            from app.admin.v2_controls import set_control
            await set_control(db, "global", False)
        elif fault == "unknown_operation":
            (await db.get(Operation, revision.operation_id)).upstream_status = None
        elif fault == "viewset_held":
            (await db.scalar(select(ViewSet).where(ViewSet.design_id == design_id))).held_units = 1
    with pytest.raises(CleanupError):
        async with factory.begin() as db:
            await module.inspect_designs(db, cfg, writers_stopped=fault != "missing_ack", now=now)
    async with factory() as db: assert (await db.get(Design, design_id)).title == PRIVATE


@pytest.mark.parametrize("policy", ["unlimited", "not_elapsed", "restored"])
async def test_only_fixed_elapsed_trash_deadline_qualifies(factory, tmp_path, policy):
    owner, design_id, _, _, _, _, _, cfg, now = await setup_content(factory, tmp_path)
    async with factory.begin() as db:
        design = await db.get(Design, design_id)
        if policy == "unlimited": design.trash_restore_until = None
        elif policy == "not_elapsed": design.trash_restore_until = now + timedelta(days=3)
        else: design.deleted_at = design.trash_restore_until = None
    assert not (await inspect(factory, cfg, now))["candidates"]


async def test_changed_review_is_refused_and_partial_failure_rolls_back_triggers(factory, tmp_path, monkeypatch):
    owner, design_id, _, _, _, _, _, cfg, now = await setup_content(factory, tmp_path)
    report = await inspect(factory, cfg, now)
    async with factory.begin() as db: (await db.get(Design, design_id)).title += " changed"
    with pytest.raises(CleanupError, match="inventory_changed"): await apply(factory, cfg, now, owner, report)
    report = await inspect(factory, cfg, now)
    before = await fingerprints(factory)
    original = module.receipt
    async def fail(*args, **kwargs): raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(module, "receipt", fail)
    async with factory.begin() as db:
        schema_before = await read_catalog(db)
        with pytest.raises(RuntimeError, match="synthetic"):
            await module.scrub_designs(db, cfg, report["digest"], owner, REASON, writers_stopped=True, now=now)
        assert await read_catalog(db) == schema_before
    assert await fingerprints(factory) == before
    monkeypatch.setattr(module, "receipt", original)


@pytest.mark.parametrize("model,field", [(InputRecord,"snapshot"),(Revision,"recipe"),(EditorCheckpoint,"document"),(LedgerEntry,"details")])
async def test_immutable_guards_remain_enabled_after_cleanup(factory, tmp_path, model, field):
    owner, _, revision, _, snapshot, checkpoint, _, cfg, now = await setup_content(factory, tmp_path)
    await apply(factory, cfg, now, owner, await inspect(factory, cfg, now))
    async with factory.begin() as db:
        query = update(model).values(**{field:{"illegal":"mutation"}})
        with pytest.raises(DBAPIError): await db.execute(query)


async def test_cleanup_runs_with_valid_production_configuration_without_storage_io(factory, tmp_path):
    owner, _, _, _, _, _, _, cfg, now = await setup_content(factory, tmp_path)
    cfg.app_env="production"; cfg.v2_runtime_mode="container"
    cfg.v2_container_user_ids=[owner]; cfg.v2_container_root="/srv/kigcraft/runtime"
    cfg.v2_container_image="runtime@sha256:"+"a"*64; cfg.v2_egress_image="egress@sha256:"+"b"*64
    cfg.v2_egress_hosts=["model.example.com"]
    cfg.v2_storage_dir=str(tmp_path/"absent-storage")
    cfg.v2_asset_backend="minio"
    report=await inspect(factory,cfg,now)
    assert (await apply(factory,cfg,now,owner,report))["count"]==1


async def test_blank_title_cleanup_invalidates_existing_trash_cursor(factory, tmp_path):
    owner, design_id, revision, _, _, _, _, cfg, now = await setup_content(factory, tmp_path)
    async with factory.begin() as db:
        (await db.get(Design, design_id)).title = ""
        asset = await db.get(Asset, revision.clean_asset_id)
    await shared_design(factory, owner, asset, now, "unlimited")
    async with factory() as db:
        _, _, cursor = await list_designs(db, owner, status="trash", limit=1)
        assert cursor is not None
    await apply(factory, cfg, now, owner, await inspect(factory, cfg, now))
    async with factory() as db:
        with pytest.raises(ApplicationError, match="design_list_changed"):
            await list_designs(db, owner, status="trash", cursor=cursor, limit=1)


async def test_retired_design_cannot_be_restored_by_sql_or_downgrade(factory, tmp_path):
    import os, subprocess, sys
    from .test_migrations import ROOT
    owner, design_id, _, _, _, _, _, cfg, now = await setup_content(factory, tmp_path)
    await apply(factory, cfg, now, owner, await inspect(factory, cfg, now))
    async with factory.begin() as db:
        with pytest.raises(DBAPIError):
            await db.execute(update(Design).where(Design.id==design_id).values(deleted_at=None,trash_restore_until=None))
    url = factory.kw["bind"].url.render_as_string(hide_password=False)
    result = subprocess.run([sys.executable, "-c",
        "from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0024')"],
        cwd=ROOT, env={**os.environ,"V2_DATABASE_URL":url}, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and "Refuse to discard content retirement markers" in result.stderr
    async with factory() as db:
        assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
        assert (await db.get(Design,design_id)).content_purged_at is not None


async def test_backup_dependencies_retain_native_and_asset_identities_after_scrub(factory, tmp_path):
    import shutil
    from app.maintenance.backup_bundle import dependencies
    from app.maintenance.container_backup import owned_threads
    owner, _, revision, extra, _, _, thread, cfg, now = await setup_content(factory, tmp_path)
    runtime_root = tmp_path/"runtime"
    stage = runtime_root/revision.operation_id/"staging"
    stage.mkdir(parents=True)
    async with factory() as db:
        descriptor = (await db.get(Operation,revision.operation_id)).staged_outputs["main_image"]
    shutil.copyfile(Path(cfg.v2_storage_dir)/"staging"/descriptor["filename"],stage/descriptor["filename"])
    await apply(factory,cfg,now,owner,await inspect(factory,cfg,now))
    async with factory() as db:
        files, threads, _ = await dependencies(db, {"runtime":runtime_root,"assets":Path(cfg.v2_storage_dir)})
        owners = await owned_threads(db)
    assert thread in threads and owners[thread] == owner
    assert "assets/uploads/"+extra+".jpg" in files
    assert "runtime/"+revision.operation_id+"/staging/"+descriptor["filename"] in files
