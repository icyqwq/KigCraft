import json
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from app.db.models import ProjectDraft, ProjectReference, ReferenceAsset, User
from app.core.time import utc_now
from app.maintenance.legacy_import import apply_inventory
from app.maintenance.legacy_materials import reference_key
from app.maintenance.v2_migration import InventoryError, inventory
from .test_legacy_inventory import seed, fingerprints
from .test_migrations import migration_database
from .test_operations import factory


async def prepared(factory, tmp_path):
    ids = await seed(factory, tmp_path / "generated")
    reference_root = tmp_path / "references"
    path = reference_root / "upload" / "original.png"
    path.parent.mkdir(parents=True)
    Image.new("RGB", (19, 13), "coral").save(path)
    async with factory.begin() as db:
        ref = await db.scalar(select(ReferenceAsset).where(ReferenceAsset.user_id == ids["owner"]))
        ref.object_key = "references/upload/original.png"
        ref.file_name = "PRIVATE ORIGINAL NAME.png"
        project_ref = ProjectReference(project_id=ids["project"], kind="hair", object_key=ref.object_key,
                                       mime_type="image/png", width=19, height=13)
        db.add(project_ref)
        owner = await db.get(User, ids["owner"])
        owner.password_hash = "test-placeholder"
        owner.email_verified_at = utc_now()
        await db.flush()
        ids.update(reference=ref.id, project_reference=project_ref.id)
    return ids, reference_root, path


async def scan(factory, tmp_path, ids, reference_root):
    async with factory() as db:
        return await inventory(db, ids["owner"], asset_root=tmp_path / "generated", reference_root=reference_root)


async def test_reference_content_and_draft_snapshot_are_repeatable_private_and_read_only(factory, tmp_path):
    ids, root, path = await prepared(factory, tmp_path)
    before = await fingerprints(factory)
    report = await scan(factory, tmp_path, ids, root)
    assert report == await scan(factory, tmp_path, ids, root)
    assert await fingerprints(factory) == before
    assert report["format"] == 2
    reference = report["ancillary"]["references"][0]
    linked = report["ancillary"]["project_references"][0]
    assert reference["image"] == linked["image"]
    assert reference["image"]["state"] == "available" and reference["image"]["width"] == 19
    assert reference["issues"] == linked["issues"] == []
    assert report["ancillary"]["drafts"][0]["format_state"] == "uninterpreted"
    assert report["ancillary"]["drafts"][0]["replay"] is False
    payload = json.dumps(report)
    assert all(value not in payload for value in (
        "PRIVATE ORIGINAL NAME", "original.png", str(root), '"private"', ids["other"], ids["foreign_project"]
    ))


async def test_missing_reference_root_is_explicit_and_generated_root_is_not_used(factory, tmp_path):
    ids, root, path = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, None)
    assert report["ancillary"]["references"][0]["image"]["state"] == "root_not_configured"
    assert report["issues"]["image_root_not_configured"] == 2


@pytest.mark.parametrize("defect,issue", [
    ("missing", "image_missing"), ("broken", "image_invalid_image"),
    ("mime", "stored_mime_mismatch"), ("dimensions", "stored_dimensions_mismatch"),
    ("unsafe", "asset_locator_unmapped"), ("foreign_project", "asset_owner_conflict"),
    ("foreign_reference", "asset_owner_conflict"),
])
async def test_reference_problems_are_reported_without_foreign_identity(factory, tmp_path, defect, issue):
    ids, root, path = await prepared(factory, tmp_path)
    async with factory.begin() as db:
        linked = await db.get(ProjectReference, ids["project_reference"])
        if defect == "missing":
            path.unlink()
        elif defect == "broken":
            path.write_bytes(b"not a png")
        elif defect == "mime":
            linked.mime_type = "image/jpeg"
        elif defect == "dimensions":
            linked.height = 7
        elif defect == "unsafe":
            linked.object_key = "references/upload/../private.png"
        elif defect == "foreign_project":
            db.add(ProjectReference(project_id=ids["foreign_project"], kind="hair", object_key=linked.object_key,
                                    mime_type="image/png", width=19, height=13))
        else:
            (await db.get(ReferenceAsset, ids["reference"])).user_id = ids["other"]
    report = await scan(factory, tmp_path, ids, root)
    linked = report["ancillary"]["project_references"][0]
    assert issue in linked["issues"] and report["issues"][issue] >= 1
    if defect.startswith("foreign"):
        assert linked["image"] == {"state": "unmapped"}
        assert ids["other"] not in json.dumps(report) and ids["foreign_project"] not in json.dumps(report)


@pytest.mark.parametrize("change", ["bytes", "name", "draft", "reference_kind"])
async def test_reviewed_apply_detects_reference_bytes_and_opaque_metadata_change(factory, tmp_path, change):
    ids, root, path = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    async with factory.begin() as db:
        if change == "bytes":
            Image.new("RGB", (19, 13), "blue").save(path)
        elif change == "name":
            (await db.get(ReferenceAsset, ids["reference"])).file_name = "new name.png"
        elif change == "draft":
            (await db.get(ProjectDraft, ids["project"])).data = {"unknown_version": 99, "private": "new"}
        else:
            (await db.get(ProjectReference, ids["project_reference"])).kind = "eyes"
    before = await fingerprints(factory)
    with pytest.raises(InventoryError, match="legacy_inventory_changed"):
        async with factory.begin() as db:
            await apply_inventory(db, ids["owner"], asset_root=tmp_path / "generated", reference_root=root,
                                  storage_root=tmp_path / "v2", expected_digest=report["inventory_digest"])
    assert await fingerprints(factory) == before and not (tmp_path / "v2").exists()


async def test_apply_receipt_does_not_misrepresent_reference_or_draft_conversion(factory, tmp_path):
    ids, root, path = await prepared(factory, tmp_path)
    async with factory.begin() as db:
        (await db.get(ProjectDraft, ids["project"])).data = ["old opaque", {"private": "value"}]
    report = await scan(factory, tmp_path, ids, root)
    draft = report["ancillary"]["drafts"][0]
    assert draft["data_state"] == "non_object" and draft["issues"] == ["draft_non_object"]
    async with factory.begin() as db:
        receipt = await apply_inventory(db, ids["owner"], asset_root=tmp_path / "generated", reference_root=root,
                                        storage_root=tmp_path / "v2", expected_digest=report["inventory_digest"])
    assert receipt["preserved_only"] == {"references": 1, "project_references": 1, "drafts": 1}
    assert receipt["ancillary_imported"] is False
    async with factory() as db:
        assert (await db.get(ProjectDraft, ids["project"])).data == ["old opaque", {"private": "value"}]
    assert path.is_file()


@pytest.mark.parametrize("key", ["../x.png", "references/a/../b.png", "references/a//b.png",
                                 "references/a/C:x.png", "references/a\\b.png", "other/a/b.png"])
def test_only_exact_legacy_reference_namespace_is_supported(key):
    assert reference_key(key) is None


async def test_reference_root_cli_is_explicit_and_preserves_database(factory, migration_database, tmp_path):
    import os, subprocess, sys
    from .test_migrations import ROOT
    ids, root, path = await prepared(factory, tmp_path)
    target = tmp_path / "inventory.json"
    before = await fingerprints(factory)
    result = subprocess.run([sys.executable, "-m", "app.maintenance.v2_migration", "--owner-id", ids["owner"],
                             "--asset-root", str(tmp_path / "generated"), "--reference-root", str(root),
                             "--report", str(target)], cwd=ROOT,
                            env={**os.environ, "V2_DATABASE_URL": migration_database},
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["ancillary"]["references"][0]["image"]["state"] == "available"
    assert str(root) not in result.stdout and "PRIVATE" not in result.stdout
    assert await fingerprints(factory) == before
