import asyncio
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select, text

from app.assets.coordination import storage_writer
from app.assets.storage import upload_in_transaction, normalize_upload
from app.design.models import Asset, Operation
from app.maintenance.orphan_assets import (
    CleanupError, digest, inspect_orphans, quarantine_orphans, restore_quarantine, root_path,
)
from .test_migrations import migration_database
from .test_operations import factory, setup, quoted, submit_request
from .test_api import api


def png():
    out = io.BytesIO()
    Image.new("RGB", (12, 12), "coral").save(out, format="PNG")
    return out.getvalue()


def orphan(root, directory="uploads", content=None):
    root.mkdir(exist_ok=True)
    path = root / directory / (str(uuid4()) + ".png")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content or png())
    return path


async def inspect(factory, root):
    async with factory.begin() as db:
        return await inspect_orphans(db, root)


async def quarantine(factory, root, report):
    async with factory.begin() as db:
        return await quarantine_orphans(db, root, report["digest"])


async def restore(factory, root, result):
    async with factory.begin() as db:
        return await restore_quarantine(db, root, result["batch_id"])


async def test_registered_unreferenced_upload_and_orphans_round_trip(factory, tmp_path):
    owner, _, _ = await setup(factory)
    root = tmp_path / "assets"
    async with factory.begin() as db:
        asset = await upload_in_transaction(db, png(), owner, root)
        db.add(asset)
    original = root / asset.object_key
    before = original.read_bytes()
    files = [orphan(root, directory) for directory in ("uploads", "masters", "legacy")]
    report = await inspect(factory, root)
    assert report["registered_assets"] == 1 and len(report["candidates"]) == 3
    assert report["blocked"] is None
    batch = await quarantine(factory, root, report)
    assert batch["count"] == 3 and original.read_bytes() == before
    assert all(not file.exists() for file in files)
    manifest = root / ".quarantine" / batch["batch_id"] / "manifest.json"
    assert json.loads(manifest.read_text()) == report
    assert (await restore(factory, root, batch))["count"] == 3
    assert (await restore(factory, root, batch))["status"] == "restored"
    assert all(file.read_bytes() == png() for file in files)
    async with factory() as db:
        assert (await db.get(Asset, asset.id)).sha256 == hashlib.sha256(before).hexdigest()
    assert manifest.is_file()


async def test_inflight_upload_is_not_quarantined_before_commit(factory, tmp_path):
    owner, _, _ = await setup(factory)
    root = tmp_path / "assets"
    async with factory.begin() as writer:
        asset = await upload_in_transaction(writer, png(), owner, root)
        writer.add(asset)
        # Deliberately no flush yet: file exists but another DB session sees no row.
        assert (root / asset.object_key).exists()
        with pytest.raises(CleanupError, match="storage_writer_busy"):
            await inspect(factory, root)
    assert not (await inspect(factory, root))["candidates"]


async def test_rolled_back_upload_can_be_quarantined_without_affecting_other_assets(factory, tmp_path):
    owner, _, _ = await setup(factory)
    root = tmp_path / "assets"
    with pytest.raises(RuntimeError):
        async with factory.begin() as writer:
            asset = await upload_in_transaction(writer, png(), owner, root)
            writer.add(asset)
            await writer.flush()
            raise RuntimeError("database commit failed")
    report = await inspect(factory, root)
    assert report["candidates"][0]["key"] == asset.object_key
    result = await quarantine(factory, root, report)
    await restore(factory, root, result)
    assert (root / asset.object_key).read_bytes() == normalize_upload(png())[0]


async def test_maintenance_blocks_new_writer_until_transaction_finishes(factory, tmp_path):
    root = tmp_path / "assets"
    orphan(root)
    acquired = asyncio.Event()
    async def writer():
        async with factory.begin() as db:
            await storage_writer(db)
            acquired.set()
    async with factory.begin() as db:
        await inspect_orphans(db, root)
        task = asyncio.create_task(writer())
        await asyncio.sleep(0.1)
        assert not acquired.is_set()
    await asyncio.wait_for(task, 3)
    assert acquired.is_set()


@pytest.mark.parametrize("change", ["file", "registration", "new_file"])
async def test_changed_reviewed_inventory_never_moves_files(factory, tmp_path, change):
    owner, _, _ = await setup(factory)
    root = tmp_path / "assets"
    file = orphan(root)
    report = await inspect(factory, root)
    if change == "file":
        file.write_bytes(b"changed")
    elif change == "new_file":
        orphan(root)
    else:
        async with factory.begin() as db:
            db.add(await upload_in_transaction(db, png(), owner, root))
    with pytest.raises(CleanupError, match="inventory_changed"):
        await quarantine(factory, root, report)
    assert file.exists() and not (root / ".quarantine").exists()


@pytest.mark.parametrize("uncertainty", ["queued", "upstream", "reservation", "unknown_state"])
async def test_unconfirmed_operations_block_file_mutation(factory, tmp_path, uncertainty):
    from app.core.time import utc_now
    owner, _, branch = await setup(factory)
    opid = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    async with factory.begin() as db:
        op = await db.get(Operation, opid)
        if uncertainty != "queued":
            op.state = "failed" if uncertainty != "unknown_state" else "future_state"
            op.reservation_closed = uncertainty != "reservation"
            if uncertainty == "upstream":
                op.dispatch_intent_at = utc_now()
                op.upstream_status = None
    root = tmp_path / "assets"
    file = orphan(root, "masters")
    report = await inspect(factory, root)
    assert report["blocked"] == "operation_unconfirmed"
    with pytest.raises(CleanupError, match="operation_unconfirmed"):
        await quarantine(factory, root, report)
    assert file.exists() and not (root / ".quarantine").exists()


async def test_unknown_names_subdirectories_temporary_files_and_runtime_are_preserved(factory, tmp_path):
    root = tmp_path / "assets"
    file = orphan(root)
    (root / "uploads" / ".pending").write_bytes(b"keep")
    (root / "uploads" / "foreign.png").write_bytes(b"keep")
    (root / "uploads" / "nested").mkdir()
    (root / "runtime").mkdir()
    (root / "runtime" / "native.jsonl").write_bytes(b"keep")
    report = await inspect(factory, root)
    assert len(report["candidates"]) == 1 and len(report["skipped"]) == 3
    await quarantine(factory, root, report)
    assert not file.exists()
    assert (root / "runtime" / "native.jsonl").read_bytes() == b"keep"
    assert (root / "uploads" / ".pending").read_bytes() == b"keep"


@pytest.mark.parametrize("failure_at", [1, 2])
async def test_interrupted_move_restores_using_write_ahead_journal(factory, tmp_path, monkeypatch, failure_at):
    import app.maintenance.orphan_assets as module
    root = tmp_path / "assets"
    files = [orphan(root) for _ in range(2)]
    report = await inspect(factory, root)
    real_link = module.os.link
    calls = 0
    def interrupted_link(source, target):
        nonlocal calls
        calls += 1
        real_link(source, target)
        if calls == failure_at:
            raise OSError("simulated interruption after link")
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "link", interrupted_link)
        with pytest.raises(OSError):
            await quarantine(factory, root, report)
    batches = list((root / ".quarantine").iterdir())
    assert len(batches) == 1 and (batches[0] / "manifest.json").is_file()
    result = {"batch_id": batches[0].name}
    await restore(factory, root, result)
    assert all(file.read_bytes() == png() for file in files)
    assert not (batches[0] / "completed.json").exists()


async def test_restore_conflict_prevalidation_does_not_restore_other_files(factory, tmp_path):
    root = tmp_path / "assets"
    files = sorted([orphan(root) for _ in range(2)])
    batch = await quarantine(factory, root, await inspect(factory, root))
    files[-1].write_bytes(b"new valid owner data")
    with pytest.raises(CleanupError, match="object_changed"):
        await restore(factory, root, batch)
    assert not files[0].exists()
    assert files[-1].read_bytes() == b"new valid owner data"


@pytest.mark.parametrize("defect", ["digest", "traversal", "scope"])
async def test_invalid_journal_is_rejected(factory, tmp_path, defect):
    root = tmp_path / "assets"
    file = orphan(root)
    batch = await quarantine(factory, root, await inspect(factory, root))
    manifest = root / ".quarantine" / batch["batch_id"] / "manifest.json"
    report = json.loads(manifest.read_text())
    if defect == "scope":
        report["database"] = "other"
    else:
        report["candidates"][0]["key"] = "../outside.png"
    if defect != "digest":
        report.pop("digest")
        report["digest"] = digest(report)
    manifest.write_text(json.dumps(report))
    with pytest.raises(CleanupError):
        await restore(factory, root, batch)
    assert not file.exists()


async def test_old_snapshot_is_refused(factory, tmp_path):
    root = tmp_path / "assets"
    orphan(root)
    async with factory.begin() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        with pytest.raises(CleanupError, match="read_committed_required"):
            await inspect_orphans(db, root)


async def test_private_upload_endpoint_uses_coordinated_installation(api, tmp_path):
    from app.core.config import get_settings
    client, factory, _, _, _ = api
    root = Path(get_settings().v2_storage_dir)
    root.mkdir()
    async with factory.begin() as db:
        await inspect_orphans(db, root)
        request = asyncio.create_task(client.post("/api/v2/assets/uploads", files={"file": ("ref.png", png(), "image/png")}))
        await asyncio.sleep(0.1)
        assert not request.done() and not list(root.glob("uploads/*"))
    response = await asyncio.wait_for(request, 3)
    assert response.status_code == 200
    assert not (await inspect(factory, root))["candidates"]


def test_linked_directory_is_refused(tmp_path, monkeypatch):
    import app.maintenance.orphan_assets as module
    root = tmp_path / "assets"
    root.mkdir()
    monkeypatch.setattr(module, "linked", lambda path: path == root)
    with pytest.raises(CleanupError, match="unsafe_storage_path"):
        root_path(root)



async def test_restore_rechecks_asset_registry_even_when_file_is_missing(factory, tmp_path):
    owner, _, _ = await setup(factory)
    root = tmp_path / "assets"
    file = orphan(root)
    batch = await quarantine(factory, root, await inspect(factory, root))
    async with factory.begin() as db:
        db.add(Asset(id=str(uuid4()), owner_id=owner, object_key=file.relative_to(root).as_posix(),
            sha256=hashlib.sha256(b"later committed image").hexdigest(), mime_type="image/png",
            kind="reference", width=12, height=12, byte_size=21, status="ready"))
    with pytest.raises(CleanupError, match="registered_asset_conflict"):
        await restore(factory, root, batch)
    assert not file.exists()


async def test_ambiguous_registered_path_refuses_inventory(factory, tmp_path):
    owner, _, _ = await setup(factory)
    root = tmp_path / "assets"
    file = orphan(root)
    async with factory.begin() as db:
        db.add(Asset(id=str(uuid4()), owner_id=owner, object_key="uploads/../uploads/" + file.name,
            sha256=hashlib.sha256(file.read_bytes()).hexdigest(), mime_type="image/png",
            kind="reference", width=12, height=12, byte_size=file.stat().st_size, status="ready"))
    with pytest.raises(CleanupError, match="unsafe_storage_path"):
        await inspect(factory, root)
    assert file.exists()


async def test_cancelled_normalization_never_leaves_a_late_filesystem_writer(factory, tmp_path, monkeypatch):
    import threading
    import app.assets.storage as storage
    owner, _, _ = await setup(factory)
    entered, released, done = threading.Event(), threading.Event(), threading.Event()
    real = storage.normalize_upload
    def slow(data):
        entered.set()
        released.wait(3)
        try:
            return real(data)
        finally:
            done.set()
    monkeypatch.setattr(storage, "normalize_upload", slow)
    root = tmp_path / "assets"
    async def upload():
        async with factory.begin() as db:
            db.add(await upload_in_transaction(db, png(), owner, root))
    task = asyncio.create_task(upload())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        released.set()
    assert await asyncio.to_thread(done.wait, 3)
    assert not root.exists()


async def test_real_linked_directory_is_never_traversed(factory, tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    marker = orphan(external)
    try:
        (root / "uploads").symlink_to(external / "uploads", target_is_directory=True)
    except OSError:
        pytest.skip("host does not allow creating a symlink")
    with pytest.raises(CleanupError, match="unsafe_storage_path"):
        await inspect(factory, root)
    assert marker.read_bytes() == png()
