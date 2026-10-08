import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select, text, func
from app.auth.sessions import _hash_token
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession, ProjectDraft, ProjectReference, ReferenceAsset
from app.design.models import Asset, LegacyMaterial
from app.design.legacy_materials import list_materials, preserved_draft
from app.maintenance.legacy_import import apply_inventory
from app.maintenance.v2_migration import InventoryError
from .test_api import api
from .test_legacy_materials import prepared, scan
from .test_legacy_inventory import fingerprints
from .test_migrations import migration_database
from .test_operations import factory


async def apply(factory, tmp_path, ids, root, report, include=True):
    async with factory.begin() as db:
        return await apply_inventory(db, ids["owner"], asset_root=tmp_path / "generated",
            reference_root=root, storage_root=tmp_path / "v2", expected_digest=report["inventory_digest"],
            include_materials=include)


async def test_opt_in_import_preserves_originals_and_replays_without_business_writes(factory, tmp_path):
    ids, root, image = await prepared(factory, tmp_path)
    async with factory.begin() as db:
        (await db.get(ProjectDraft, ids["project"])).data = ["unknown version", {"private": "RAW DRAFT"}]
    report = await scan(factory, tmp_path, ids, root)
    default = await apply(factory, tmp_path, ids, root, report, False)
    assert default["materials"] is None
    before = await fingerprints(factory)
    imported = await apply(factory, tmp_path, ids, root, report)
    assert imported["materials"] == {"imported": 3, "replayed": 0, "skipped": [],
        "references_available": 2, "drafts_preserved": 1, "editor_drafts_converted": 0}
    after = await fingerprints(factory)
    assert {name for name in before if before[name] != after[name]} == {"v2_assets", "v2_legacy_materials"}
    async with factory() as db:
        rows = list((await db.scalars(select(LegacyMaterial))).all())
        assert len(rows) == 3
        draft = next(row for row in rows if row.source_kind == "draft")
        assert draft.source_summary["record"]["data"] == ["unknown version", {"private": "RAW DRAFT"}]
        for row in rows:
            if row.asset_id:
                asset = await db.get(Asset, row.asset_id)
                assert asset.owner_id == ids["owner"] and asset.kind == "legacy_reference"
                copied = (tmp_path / "v2" / asset.object_key).read_bytes()
                assert copied == image.read_bytes() and hashlib.sha256(copied).hexdigest() == asset.sha256
                assert (asset.width, asset.height) == (19, 13)
    replay = await apply(factory, tmp_path, ids, root, report)
    assert replay["materials"]["replayed"] == 3 and replay["materials"]["imported"] == 0
    assert await fingerprints(factory) == after


@pytest.mark.parametrize("defect", ["missing", "foreign_claim", "no_root"])
async def test_unavailable_or_foreign_reference_is_not_copied(factory, tmp_path, defect):
    ids, root, image = await prepared(factory, tmp_path)
    if defect == "missing":
        image.unlink()
    elif defect == "foreign_claim":
        async with factory.begin() as db:
            db.add(ProjectReference(project_id=ids["foreign_project"], kind="hair",
                object_key="references/upload/original.png", mime_type="image/png", width=19, height=13))
    else:
        root = None
    report = await scan(factory, tmp_path, ids, root)
    receipt = await apply(factory, tmp_path, ids, root, report)
    assert receipt["materials"]["imported"] == 1
    assert receipt["materials"]["references_available"] == 0
    assert len(receipt["materials"]["skipped"]) == 2
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Asset).where(Asset.kind == "legacy_reference")) == 0
        assert (await db.scalar(select(LegacyMaterial))).source_kind == "draft"


@pytest.mark.parametrize("change", ["source_bytes", "source_metadata", "source_draft", "copied_bytes", "copied_missing"])
async def test_changed_source_or_damaged_copy_refuses_replay_without_overwriting(factory, tmp_path, change):
    ids, root, image = await prepared(factory, tmp_path)
    await apply(factory, tmp_path, ids, root, await scan(factory, tmp_path, ids, root))
    async with factory.begin() as db:
        if change == "source_metadata":
            (await db.get(ReferenceAsset, ids["reference"])).file_name = "changed.png"
        elif change == "source_draft":
            (await db.get(ProjectDraft, ids["project"])).data = {"private": "changed"}
        elif change.startswith("copied"):
            row = await db.scalar(select(LegacyMaterial).where(LegacyMaterial.source_kind == "reference"))
            asset = await db.get(Asset, row.asset_id)
            path = tmp_path / "v2" / asset.object_key
            if change == "copied_bytes":
                path.write_bytes(b"damaged")
            else:
                path.unlink()
    if change == "source_bytes":
        Image.new("RGB", (19, 13), "blue").save(image)
    report = await scan(factory, tmp_path, ids, root)
    before = await fingerprints(factory)
    with pytest.raises(InventoryError, match="legacy_(material_changed_after_import|imported_material_)"):
        await apply(factory, tmp_path, ids, root, report)
    assert await fingerprints(factory) == before


async def test_material_mapping_cannot_be_rewritten_or_deleted(factory, tmp_path):
    ids, root, _ = await prepared(factory, tmp_path)
    await apply(factory, tmp_path, ids, root, await scan(factory, tmp_path, ids, root))
    before = await fingerprints(factory)
    for statement in ["UPDATE v2_legacy_materials SET source_digest='changed'", "DELETE FROM v2_legacy_materials"]:
        with pytest.raises(Exception, match="immutable"):
            async with factory.begin() as db:
                await db.execute(text(statement))
    assert await fingerprints(factory) == before


async def test_private_catalog_pagination_download_and_asset_permissions(api, tmp_path, monkeypatch):
    client, factory, other, _, _ = api
    ids, root, original = await prepared(factory, tmp_path)
    await apply(factory, tmp_path, ids, root, await scan(factory, tmp_path, ids, root))
    monkeypatch.setenv("V2_STORAGE_DIR", str(tmp_path / "v2")); get_settings.cache_clear()
    async with factory() as db:
        rows = list((await db.scalars(select(LegacyMaterial))).all())
    draft = next(row for row in rows if row.source_kind == "draft")
    asset = next(row.asset_id for row in rows if row.asset_id)
    endpoint = "/api/v2/account/legacy-materials"
    assert (await client.get(endpoint + "?kind=all")).json()["items"] == []
    assert (await client.get(endpoint + "/" + draft.id + "/draft")).status_code == 404
    assert (await client.get("/api/v2/assets/" + asset)).status_code == 404
    token = "materials-" + uuid4().hex
    async with factory.begin() as db:
        db.add(AuthSession(user_id=ids["owner"], session_token_hash=_hash_token(token),
                           expires_at=utc_now() + timedelta(hours=1), created_ip="127.0.0.1"))
    client.cookies.clear(); client.cookies.set(get_settings().session_cookie_name, token)
    before = await fingerprints(factory)
    response = await client.get(endpoint + "?kind=all&limit=1")
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    items = response.json()["items"]; cursor = response.json()["next_cursor"]
    while cursor:
        response = await client.get(endpoint + "?kind=all&limit=1&cursor=" + cursor)
        items += response.json()["items"]; cursor = response.json()["next_cursor"]
    assert len(items) == 3 and len({row["id"] for row in items}) == 3
    assert "object_key" not in json.dumps(items) and "private" not in json.dumps(items)
    refs = (await client.get(endpoint)).json()["items"]
    assert len(refs) == 2 and all(row["asset_id"] for row in refs)
    assert (await client.get(refs[0]["image_url"])).content == original.read_bytes()
    saved = await client.get(endpoint + "/" + draft.id + "/draft")
    assert saved.status_code == 200 and saved.headers["cache-control"] == "private, no-store"
    assert saved.headers["content-disposition"].startswith("attachment;")
    assert saved.headers["x-content-type-options"] == "nosniff"
    assert saved.json()["record"]["data"] == {"private": "draft"}
    assert saved.json()["replay"] is False and saved.json()["editor_document"] is False
    assert (await client.get(endpoint + "?cursor=invalid")).status_code == 422
    assert (await client.get(endpoint + "?limit=101")).status_code == 422
    assert await fingerprints(factory) == before
    client.cookies.clear()
    assert (await client.get(endpoint)).status_code == 401


async def test_material_asset_owner_constraint_rejects_cross_account_reference(factory, tmp_path):
    from sqlalchemy.exc import IntegrityError
    ids, root, _ = await prepared(factory, tmp_path)
    await apply(factory, tmp_path, ids, root, await scan(factory, tmp_path, ids, root))
    async with factory() as db:
        asset_id = await db.scalar(select(LegacyMaterial.asset_id).where(LegacyMaterial.asset_id.is_not(None)))
    before = await fingerprints(factory)
    with pytest.raises(IntegrityError, match="fk_v2_legacy_material_asset_owner"):
        async with factory.begin() as db:
            db.add(LegacyMaterial(owner_id=ids["other"], source_kind="reference", source_id=str(uuid4()),
                source_digest="a"*64, source_summary={}, asset_id=asset_id))
            await db.flush()
    assert await fingerprints(factory) == before


async def test_cli_material_opt_in_and_repeat_receipt(factory, migration_database, tmp_path):
    import os
    import subprocess
    import sys
    from .test_migrations import ROOT
    ids, root, _ = await prepared(factory, tmp_path)
    report = await scan(factory, tmp_path, ids, root)
    def cli(name, *extra):
        return subprocess.run([sys.executable, "-m", "app.maintenance.v2_migration",
            "--owner-id", ids["owner"], "--asset-root", str(tmp_path / "generated"),
            "--reference-root", str(root), "--report", str(tmp_path / name), *extra],
            cwd=ROOT, env=os.environ | {"V2_DATABASE_URL": migration_database, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=30)
    refused = cli("refused.json", "--include-materials")
    assert refused.returncode == 2 and "--include-materials requires --apply" in refused.stderr
    assert not (tmp_path / "refused.json").exists()
    args = ["--apply", "--include-materials", "--expected-digest", report["inventory_digest"],
            "--storage-root", str(tmp_path / "v2")]
    first = cli("first.json", *args)
    assert first.returncode == 0, first.stderr
    summary = json.loads(first.stdout)
    assert summary["materials"]["imported"] == 3 and summary["materials"]["editor_drafts_converted"] == 0
    receipt = json.loads((tmp_path / "first.json").read_text(encoding="utf-8"))
    assert receipt["ancillary_imported"] is True
    assert receipt["preserved_only"] == {"references": 0, "project_references": 0, "drafts": 1}
    assert receipt["wallet_changed"] is False and receipt["model_calls"] == 0
    before = await fingerprints(factory)
    second = cli("second.json", *args)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["materials"]["replayed"] == 3
    assert await fingerprints(factory) == before
    assert "PRIVATE" not in first.stdout + second.stdout and "private" not in first.stdout + second.stdout
