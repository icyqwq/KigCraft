"""Remote-object complete backup components. No live S3, pg_dump, restore or Docker."""
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.assets import repository
from app.auth.models import UserRole
from app.core.time import utc_now
from app.db.models import User
from app.design.models import Asset
from app.design.service import ApplicationError
from app.maintenance import backup_bundle as backup, backup_retention as retention, restore_bundle as restore
from app.maintenance.backup_bundle import BackupError
from .test_operations import factory
from .test_migrations import migration_database
from .test_backup_bundle import prepared as local_prepared, create as local_create
from .test_container_backup import prepared as container_prepared, create as container_create
from .test_restore_bundle import fake_create, fake_import, fake_validate
from .test_minio_assets import Objects, config
from .test_legacy_inventory import fingerprints


async def remote_assets(factory, cfg, roots, tmp_path, monkeypatch):
    objects = Objects()
    async with factory() as db:
        assets = list(await db.scalars(select(Asset).order_by(Asset.id)))
    original = roots["assets"]
    for asset in assets:
        objects.objects["v2/" + asset.object_key] = (original / asset.object_key).read_bytes()
    selected = config(tmp_path)
    for field in ("v2_asset_backend", "v2_s3_endpoint_url", "v2_s3_bucket",
                  "v2_s3_prefix", "v2_s3_access_key_id", "v2_s3_secret_access_key", "v2_s3_region"):
        setattr(cfg, field, getattr(selected, field))
    cfg.v2_storage_dir = str(tmp_path / "never-populated-cache")
    roots["assets"] = tmp_path / "never-populated-cache"
    monkeypatch.setattr(repository, "make_client", lambda _: objects)
    return objects, assets


async def prepare(factory, tmp_path, monkeypatch, mode="local"):
    if mode == "container":
        cfg, roots, base, owners, ids, ops, states, runtime = await container_prepared(factory, tmp_path, monkeypatch)
        async def create(destination):
            return await container_create(factory, cfg, roots, destination, runtime)
    else:
        owner, _, _, roots, cfg = await local_prepared(factory, tmp_path)
        owners = [owner]
        async def create(destination):
            return await local_create(factory, cfg, destination, roots)
    objects, assets = await remote_assets(factory, cfg, roots, tmp_path, monkeypatch)
    return cfg, roots, owners, objects, assets, create


@pytest.mark.parametrize("mode", ["local", "container"])
async def test_complete_bundle_restores_assets_offline_with_execution_disabled(factory, tmp_path, monkeypatch, mode):
    cfg, roots, owners, objects, assets, create = await prepare(factory, tmp_path, monkeypatch, mode)
    before = await fingerprints(factory)
    directory = tmp_path / "backup"
    result = await create(directory)
    assert not roots["assets"].exists()
    manifest = backup.verify_bundle(directory, expected_manifest_sha256=result["manifest_sha256"])
    metadata = manifest["asset_storage"]
    assert metadata["backend"] == "minio" and len(metadata["objects"]) == len(assets)
    assert metadata["objects"] == {key: value for key, value in manifest["files"].items() if key.startswith("assets/")}
    serialized = (directory / "manifest.json").read_text(encoding="utf-8")
    assert "synthetic-secret" not in serialized and "synthetic-access" not in serialized
    assert "minio.invalid" not in serialized
    assert len(objects.calls) == 2 * len(assets)
    assert all(method == "get" for method, _ in objects.calls)
    remote_before = dict(objects.objects)
    objects.fail_get = True  # Offline verification/restoration must not need the original bucket.
    destination = tmp_path / "restored"
    restored = await restore.restore_bundle(directory, destination, "postgresql://test@localhost/postgres",
        result["manifest_sha256"], create_database=fake_create, restore=fake_import, validate=fake_validate)
    assert restored["status"] == "restored_execution_disabled"
    for asset in assets:
        assert (destination / "assets" / asset.object_key).read_bytes() == remote_before["v2/" + asset.object_key]
    settings = (destination / "execution-disabled.env.example").read_text(encoding="utf-8")
    assert 'V2_ASSET_BACKEND="filesystem"' in settings and 'V2_RUNTIME_MODE="disabled"' in settings
    receipt = json.loads((destination / "restore-receipt.json").read_text(encoding="utf-8"))
    assert receipt["source_asset_storage"] == metadata and receipt["restored_asset_backend"] == "filesystem"
    if mode == "container":
        assert set(manifest["accounts"]) == set(owners)
        for owner in owners:
            assert (destination / "accounts" / owner / "profile" / "state_5.sqlite").exists()
    assert objects.objects == remote_before and await fingerprints(factory) == before


@pytest.mark.parametrize("fault", ["missing", "corrupt", "changed_after_copy", "expired_missing"])
async def test_remote_fault_never_uses_cache_or_completes_bundle(factory, tmp_path, monkeypatch, fault):
    cfg, roots, _, objects, assets, create = await prepare(factory, tmp_path, monkeypatch)
    asset = assets[0]
    key = "v2/" + asset.object_key
    cache = roots["assets"] / asset.object_key
    cache.parent.mkdir(parents=True)
    cache.write_bytes(objects.objects[key])
    if fault in {"missing", "expired_missing"}:
        objects.objects.pop(key)
    if fault == "expired_missing":
        async with factory.begin() as db:
            (await db.get(Asset, asset.id)).status = "expired"
    if fault == "corrupt":
        objects.objects[key] = b"corrupt"
    if fault == "changed_after_copy":
        get = objects.get_object
        calls = 0
        def changing(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                objects.objects[key] = b"changed"
            return get(**kwargs)
        objects.get_object = changing
    directory = tmp_path / "backup"
    before = await fingerprints(factory)
    with pytest.raises((repository.StorageUnavailable, ApplicationError)):
        await create(directory)
    assert (directory / "INCOMPLETE").exists() and not (directory / "manifest.json").exists()
    with pytest.raises(BackupError, match="incomplete_backup"):
        backup.verify_bundle(directory)
    assert await fingerprints(factory) == before and cache.exists()


async def test_expired_included_purged_excluded_and_remote_bytes_not_deleted(factory, tmp_path, monkeypatch):
    cfg, roots, owners, objects, assets, create = await prepare(factory, tmp_path, monkeypatch)
    async with factory.begin() as db:
        (await db.get(Asset, assets[0].id)).status = "expired"
        purged_id = str(uuid4())
        db.add(Asset(id=purged_id, owner_id=owners[0], object_key="uploads/" + purged_id + ".png",
            sha256="0" * 64, byte_size=1, width=1, height=1, mime_type="image/png", kind="reference", status="purged"))
    await create(tmp_path / "backup")
    manifest = backup.verify_bundle(tmp_path / "backup")
    assert len(manifest["asset_storage"]["objects"]) == 1
    assert manifest["absent_assets"] == [{"asset_id": purged_id, "state": "purged"}]
    assert len(objects.objects) == 1


@pytest.mark.parametrize("change", ["omitted_object", "unknown_field", "oversize"])
async def test_remote_manifest_contract_is_verified_offline(factory, tmp_path, monkeypatch, change):
    cfg, roots, _, objects, assets, create = await prepare(factory, tmp_path, monkeypatch)
    directory = tmp_path / "backup"
    await create(directory)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if change == "omitted_object":
        manifest["asset_storage"]["objects"] = {}
    elif change == "unknown_field":
        manifest["asset_storage"]["credential"] = "not-allowed"
    else:
        next(iter(manifest["asset_storage"]["objects"].values()))["bytes"] = 129 * 1024**2
    path.write_text(json.dumps(manifest), encoding="utf-8")
    objects.fail_get = True
    with pytest.raises(BackupError, match="invalid_asset_storage_manifest"):
        backup.verify_bundle(directory)


async def test_minio_bundle_retention_keeps_two_verified_copies_without_remote_access(factory, tmp_path, monkeypatch):
    import shutil
    cfg, roots, owners, objects, assets, create = await prepare(factory, tmp_path, monkeypatch, "container")
    root = tmp_path / "backups"
    root.mkdir()
    await create(root / "b0")
    now = utc_now()
    for index in range(3):
        path = root / f"b{index}"
        if index:
            shutil.copytree(root / "b0", path)
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        manifest["created_at"] = (now - timedelta(days=40-index*10)).isoformat()
        (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    async with factory.begin() as db:
        (await db.get(User, owners[0])).role = UserRole.admin
    cfg.v2_backup_retention_days = 7
    objects.fail_get = True
    before = len(objects.calls)
    async with factory.begin() as db:
        report = await retention.inspect_backups(db, cfg, root, users_stopped=True, now=now)
        assert len(report["keep"]) == 2 and len(report["candidates"]) == 1
        result = await retention.retire_backups(db, cfg, root, report["digest"], owners[0],
            "Reviewed bundle retention", users_stopped=True, now=now)
    async with factory.begin() as db:
        result = await retention.purge_backups(db, cfg, root, result["receipt_id"], owners[0],
            "Reviewed bundle retention", users_stopped=True, now=now)
    assert result["count"] == 1 and not (root / "b0").exists()
    assert len(objects.calls) == before
    for name in ("b1", "b2"):
        assert backup.verify_bundle(root / name)["asset_storage"]["backend"] == "minio"


async def test_restored_asset_directory_can_be_copied_to_a_new_minio_prefix(factory, tmp_path, monkeypatch):
    from app.maintenance import minio_migration as migration
    cfg, roots, owners, objects, assets, create = await prepare(factory, tmp_path, monkeypatch)
    directory = tmp_path / "backup"
    result = await create(directory)
    destination = tmp_path / "restored"
    await restore.restore_bundle(directory, destination, "postgresql://test@localhost/postgres",
        result["manifest_sha256"], create_database=fake_create, restore=fake_import, validate=fake_validate)
    # Restore imports the same asset registry. This fixture retains that registry
    # while substituting database creation/import; no persistent restore is run.
    async with factory.begin() as db:
        (await db.get(User, owners[0])).role = UserRole.admin
    cfg.v2_s3_prefix = "restored-v2"
    async with factory.begin() as db:
        report = await migration.inspect_assets(db, cfg, destination / "assets", writers_stopped=True)
    async with factory.begin() as db:
        await migration.copy_assets(db, cfg, destination / "assets", report["digest"], owners[0],
            "Reviewed isolated restore", writers_stopped=True)
    async with factory.begin() as db:
        verified = await migration.verify_assets(db, cfg, writers_stopped=True)
    assert verified["verified_count"] == len(assets)
    for asset in assets:
        assert objects.objects["restored-v2/" + asset.object_key] == objects.objects["v2/" + asset.object_key]
