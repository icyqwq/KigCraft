import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from app.maintenance.legacy_materials_archive import archive_materials, verify_archive
from app.maintenance.v2_migration import InventoryError
from .test_legacy_materials import prepared, scan
from .test_legacy_inventory import fingerprints
from .test_migrations import migration_database
from .test_operations import factory


async def archive(factory, tmp_path, ids, root, report, destination):
    async with factory.begin() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        return await archive_materials(db, ids["owner"], asset_root=tmp_path / "generated",
                                      reference_root=root, destination=destination,
                                      expected_digest=report["inventory_digest"])


async def test_archive_copies_exact_original_materials_and_verifies_after_relocation(factory, tmp_path):
    import shutil
    ids, root, source = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    before = await fingerprints(factory)
    results = []
    for name in ("first", "second"):
        results.append(await archive(factory, tmp_path, ids, root, report, tmp_path / name))
    assert results[0] == results[1]  # Same snapshot has a stable external manifest digest.
    result = results[0]
    assert result["copied_images"] == 2 and result["unavailable_images"] == 0
    assert result["database_writes"] is False and result["drafts_replayed"] is False
    assert await fingerprints(factory) == before
    document = json.loads((tmp_path / "first/materials.json").read_text(encoding="utf-8"))
    assert document["materials"]["drafts"][0]["data"] == {"private": "draft"}
    assert document["materials"]["references"][0]["file_name"] == "PRIVATE ORIGINAL NAME.png"
    assert not {"password_hash", "email", "auth_sessions"} & document.keys()
    assert len(document["projects"]) == 1 and document["projects"][0]["id"] == ids["project"]
    assert all(file.read_bytes() == source.read_bytes() for file in (tmp_path / "first/images").iterdir())
    shutil.copytree(tmp_path / "first", tmp_path / "relocated")
    checked = verify_archive(tmp_path / "relocated", expected_owner=ids["owner"],
                             expected_manifest_sha256=result["manifest_sha256"])
    assert checked["status"] == "verified" and checked["inventory_digest"] == report["inventory_digest"]
    assert not (tmp_path / "relocated/INCOMPLETE").exists()


@pytest.mark.parametrize("defect", ["image", "missing", "metadata", "inventory", "extra", "manifest", "incomplete", "owner"])
async def test_verification_rejects_tampering_missing_data_and_wrong_owner(factory, tmp_path, defect):
    ids, root, source = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    destination = tmp_path / "archive"
    result = await archive(factory, tmp_path, ids, root, report, destination)
    if defect == "image":
        next((destination / "images").iterdir()).write_bytes(b"broken")
    elif defect == "missing":
        next((destination / "images").iterdir()).unlink()
    elif defect == "metadata":
        (destination / "materials.json").write_text("{}", encoding="utf-8")
    elif defect == "inventory":
        (destination / "inventory.json").write_text("{}", encoding="utf-8")
    elif defect == "extra":
        (destination / "unlisted.txt").write_text("not part of snapshot", encoding="utf-8")
    elif defect == "manifest":
        (destination / "manifest.json").write_text("{}", encoding="utf-8")
    elif defect == "incomplete":
        (destination / "INCOMPLETE").write_text("not finished", encoding="utf-8")
    with pytest.raises(InventoryError):
        verify_archive(destination, expected_owner=ids["other"] if defect == "owner" else ids["owner"],
                       expected_manifest_sha256=result["manifest_sha256"])


async def test_missing_images_are_explicit_without_fabricating_draft_recovery(factory, tmp_path):
    ids, root, source = await prepared(factory, tmp_path)
    source.unlink()
    report = await scan(factory, tmp_path, ids, root)
    result = await archive(factory, tmp_path, ids, root, report, tmp_path / "archive")
    assert result["copied_images"] == 0 and result["unavailable_images"] == 2
    assert result["counts"] == {"references": 1, "project_references": 1, "drafts": 1}
    assert result["drafts_replayed"] is False
    document = json.loads((tmp_path / "archive/materials.json").read_text(encoding="utf-8"))
    assert document["materials"]["drafts"][0]["data"] == {"private": "draft"}


async def test_existing_archive_is_never_overwritten_and_stale_review_does_not_create_output(factory, tmp_path):
    ids, root, source = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    destination = tmp_path / "archive"
    await archive(factory, tmp_path, ids, root, report, destination)
    original = (destination / "manifest.json").read_bytes()
    with pytest.raises(InventoryError, match="legacy_archive_target_exists"):
        await archive(factory, tmp_path, ids, root, report, destination)
    assert (destination / "manifest.json").read_bytes() == original
    source.write_bytes(b"changed")
    with pytest.raises(InventoryError, match="legacy_inventory_changed"):
        await archive(factory, tmp_path, ids, root, report, tmp_path / "refused")
    assert not (tmp_path / "refused").exists()


async def test_failure_during_copy_leaves_incomplete_and_original_database_untouched(factory, tmp_path, monkeypatch):
    from app.maintenance import legacy_materials_archive as module
    ids, root, source = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    before = await fingerprints(factory)
    original_write = module.write_new
    def fail_copy(path, data):
        if path.parent.name == "images":
            raise OSError("fixture storage failure")
        original_write(path, data)
    monkeypatch.setattr(module, "write_new", fail_copy)
    with pytest.raises(OSError):
        await archive(factory, tmp_path, ids, root, report, tmp_path / "archive")
    assert (tmp_path / "archive/INCOMPLETE").exists()
    assert await fingerprints(factory) == before
    with pytest.raises(InventoryError, match="incomplete"):
        verify_archive(tmp_path / "archive", expected_owner=ids["owner"])


async def test_reference_root_and_archive_limits_are_required_before_creating_files(factory, tmp_path, monkeypatch):
    from app.maintenance import legacy_materials_archive as module
    ids, root, source = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, None)
    with pytest.raises(InventoryError, match="explicit_reference_root_required"):
        await archive(factory, tmp_path, ids, None, report, tmp_path / "no-root")
    report = await scan(factory, tmp_path, ids, root)
    monkeypatch.setattr(module, "MAX_ARCHIVE_IMAGE_BYTES", 1)
    with pytest.raises(InventoryError, match="legacy_archive_images_too_large"):
        await archive(factory, tmp_path, ids, root, report, tmp_path / "too-large")
    assert not (tmp_path / "no-root").exists() and not (tmp_path / "too-large").exists()


async def test_archive_and_offline_verify_cli_preserve_all_tables(factory, migration_database, tmp_path):
    import os, subprocess, sys
    from .test_migrations import ROOT
    ids, root, source = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    before = await fingerprints(factory)
    destination = tmp_path / "archive"
    receipt = tmp_path / "receipt.json"
    result = subprocess.run([sys.executable, "-m", "app.maintenance.v2_migration", "--owner-id", ids["owner"],
        "--asset-root", str(tmp_path / "generated"), "--reference-root", str(root),
        "--archive-materials", str(destination), "--expected-digest", report["inventory_digest"],
        "--report", str(receipt)], cwd=ROOT, env={**os.environ, "V2_DATABASE_URL": migration_database},
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "archived" and summary["copied_images"] == 2
    assert "PRIVATE" not in result.stdout and str(root) not in result.stdout
    verified = subprocess.run([sys.executable, "-m", "app.maintenance.legacy_materials_archive",
        str(destination), "--owner-id", ids["owner"], "--manifest-sha256", summary["manifest_sha256"]],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(verified.stdout)["status"] == "verified"
    assert await fingerprints(factory) == before


async def test_receipt_cannot_make_archive_self_inconsistent(factory, migration_database, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.maintenance.v2_migration import run
    ids, root, source = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    monkeypatch.setenv("V2_DATABASE_URL", migration_database)
    args = SimpleNamespace(owner_id=ids["owner"], asset_root=tmp_path / "generated", reference_root=root,
        public_prefix="/api/generated", apply=False, archive_materials=tmp_path / "archive",
        expected_digest=report["inventory_digest"], report=tmp_path / "archive/receipt.json")
    with pytest.raises(InventoryError, match="archive_receipt_must_be_external"):
        await run(args)
    assert not (tmp_path / "archive").exists()


async def test_reference_change_between_inventory_and_copy_is_not_archived_as_verified(factory, tmp_path, monkeypatch):
    from app.maintenance import legacy_materials_archive as module
    ids, root, source = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    original_write = module.write_new
    def replace_after_metadata(path, data):
        original_write(path, data)
        if path.name == "materials.json":
            source.write_bytes(b"changed after the snapshot inspection")
    monkeypatch.setattr(module, "write_new", replace_after_metadata)
    with pytest.raises(InventoryError, match="legacy_reference_changed"):
        await archive(factory, tmp_path, ids, root, report, tmp_path / "archive")
    assert (tmp_path / "archive/INCOMPLETE").exists()
    assert not (tmp_path / "archive/manifest.json").exists()


async def test_receipt_failure_preserves_complete_verifiable_archive(factory, migration_database, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.maintenance.v2_migration import run
    ids, root, source = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    before = await fingerprints(factory)
    monkeypatch.setenv("V2_DATABASE_URL", migration_database)
    receipt = tmp_path / "receipt.json"
    args = SimpleNamespace(owner_id=ids["owner"], asset_root=tmp_path / "generated", reference_root=root,
        public_prefix="/api/generated", apply=False, archive_materials=tmp_path / "archive",
        expected_digest=report["inventory_digest"], report=receipt)
    original_open = Path.open
    def unavailable(path, *args, **kwargs):
        if path == receipt and args and args[0] == "x":
            raise OSError("fixture report failure")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", unavailable)
    with pytest.raises(InventoryError, match="report_write_failed_archive_may_be_complete"):
        await run(args)
    result = verify_archive(tmp_path / "archive", expected_owner=ids["owner"])
    assert result["copied_images"] == 2 and result["unavailable_images"] == 0
    assert await fingerprints(factory) == before
