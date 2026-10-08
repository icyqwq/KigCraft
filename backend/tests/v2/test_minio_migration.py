"""File migration components; isolated PostgreSQL and in-memory S3, no live migration."""
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.assets import repository
from app.auth.models import UserRole
from app.db.models import User
from app.design.models import AdminAction, Asset, RuntimeControl
from app.design.service import ApplicationError
from app.maintenance import minio_migration as migration
from app.maintenance.orphan_assets import CleanupError
from .test_minio_assets import Objects, config
from .test_operations import factory, setup
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints


async def prepared(factory, tmp_path, monkeypatch, count=3):
    owner, _, _ = await setup(factory)
    other, _, _ = await setup(factory)
    cfg = config(tmp_path)
    objects = Objects()
    monkeypatch.setattr(repository, "make_client", lambda _: objects)
    source = tmp_path / "source"
    source.mkdir()
    assets = []
    async with factory.begin() as db:
        (await db.get(User, owner)).role = UserRole.admin
        db.add(RuntimeControl(scope="global", blocked=True))
        for index in range(count):
            identity = str(uuid4())
            key = "uploads/" + identity + ".png"
            data = ("asset-" + str(index)).encode()
            path = source / key
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
            asset = Asset(id=identity, owner_id=owner if index % 2 else other,
                object_key=key, sha256=hashlib.sha256(data).hexdigest(),
                mime_type="image/png", kind="reference", width=1, height=1,
                byte_size=len(data), status="ready" if index % 2 else "expired")
            db.add(asset)
            assets.append(asset)
    return cfg, objects, source, owner, sorted(assets, key=lambda a:a.id)


async def inspect(factory, cfg, source, **kwargs):
    async with factory.begin() as db:
        return await migration.inspect_assets(db, cfg, source, writers_stopped=True, **kwargs)


async def copy(factory, cfg, source, owner, report, **kwargs):
    async with factory.begin() as db:
        return await migration.copy_assets(db, cfg, source, report["digest"], owner,
            "Reviewed migration", writers_stopped=True, **kwargs)


async def verify(factory, cfg):
    async with factory.begin() as db:
        return await migration.verify_assets(db, cfg, writers_stopped=True)


async def test_pages_receipts_and_complete_verification_preserve_business_data(factory, tmp_path, monkeypatch):
    cfg, objects, source, owner, assets = await prepared(factory, tmp_path, monkeypatch)
    before = await fingerprints(factory)
    report = await inspect(factory, cfg, source, limit=1)
    assert not objects.calls
    assert "synthetic-access" not in json.dumps(report) and "synthetic-secret" not in json.dumps(report)
    first = await copy(factory, cfg, source, owner, report, limit=1)
    assert await copy(factory, cfg, source, owner, report, limit=1) == first
    with pytest.raises(repository.StorageUnavailable):
        await verify(factory, cfg)
    while report["next_after"]:
        after = report["next_after"]
        report = await inspect(factory, cfg, source, after=after, limit=1)
        await copy(factory, cfg, source, owner, report, after=after, limit=1)
    result = await verify(factory, cfg)
    assert result["verified_count"] == 3 and result["purged_count"] == 0
    assert not result["configuration_changed"] and not result["source_removed"]
    for asset in assets:
        assert (source / asset.object_key).read_bytes() == objects.objects["v2/" + asset.object_key]
    after = await fingerprints(factory)
    assert {k:v for k,v in before.items() if k != "v2_admin_actions"} == {
        k:v for k,v in after.items() if k != "v2_admin_actions"}
    objects.objects.pop("v2/" + assets[0].object_key)
    with pytest.raises(repository.StorageUnavailable):
        await verify(factory, cfg)


async def test_partial_remote_failure_retries_without_overwriting_or_duplicate_receipt(factory, tmp_path, monkeypatch):
    cfg, objects, source, owner, assets = await prepared(factory, tmp_path, monkeypatch)
    report = await inspect(factory, cfg, source)
    objects.fail_get = True
    with pytest.raises(repository.StorageUnavailable):
        await copy(factory, cfg, source, owner, report)
    assert len(objects.objects) == 1
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AdminAction)) == 0
    objects.fail_get = False
    await copy(factory, cfg, source, owner, report)
    assert (await verify(factory, cfg))["verified_count"] == 3
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AdminAction)) == 1


@pytest.mark.parametrize("change", ["bytes", "target", "status", "owner", "purged"])
async def test_reviewed_batch_drift_prevents_all_puts(factory, tmp_path, monkeypatch, change):
    cfg, objects, source, owner, assets = await prepared(factory, tmp_path, monkeypatch)
    report = await inspect(factory, cfg, source)
    if change == "bytes":
        (source / assets[-1].object_key).write_bytes(b"changed")
    elif change == "target":
        cfg.v2_s3_prefix = "different"
    else:
        async with factory.begin() as db:
            row = await db.get(Asset, assets[0].id)
            if change == "owner":
                row.owner_id = owner if row.owner_id != owner else assets[1].owner_id
                if row.owner_id == assets[0].owner_id:
                    row.owner_id = next(a.owner_id for a in assets if a.owner_id != row.owner_id)
            else:
                row.status = "purged" if change == "purged" else ("ready" if row.status == "expired" else "expired")
    with pytest.raises(CleanupError, match="source_identity_mismatch|inventory_changed"):
        await copy(factory, cfg, source, owner, report)
    assert not objects.calls


async def test_existing_conflicting_remote_object_never_overwritten(factory, tmp_path, monkeypatch):
    cfg, objects, source, owner, assets = await prepared(factory, tmp_path, monkeypatch, count=1)
    report = await inspect(factory, cfg, source)
    key = "v2/" + assets[0].object_key
    objects.objects[key] = b"conflict"
    with pytest.raises(ApplicationError, match="asset_identity_conflict"):
        await copy(factory, cfg, source, owner, report)
    assert objects.objects[key] == b"conflict"


async def test_purged_records_not_resurrected_and_missing_source_refused(factory, tmp_path, monkeypatch):
    cfg, objects, source, owner, assets = await prepared(factory, tmp_path, monkeypatch, count=2)
    async with factory.begin() as db:
        (await db.get(Asset, assets[0].id)).status = "purged"
    (source / assets[0].object_key).unlink()
    report = await inspect(factory, cfg, source)
    assert len(report["purged"]) == 1
    await copy(factory, cfg, source, owner, report)
    result = await verify(factory, cfg)
    assert result["purged_count"] == 1 and result["verified_count"] == 1
    (source / assets[1].object_key).unlink()
    with pytest.raises(FileNotFoundError):
        await inspect(factory, cfg, source)


async def test_pause_ack_admin_and_limits_enforced_without_remote_io(factory, tmp_path, monkeypatch):
    cfg, objects, source, owner, assets = await prepared(factory, tmp_path, monkeypatch)
    async with factory.begin() as db:
        with pytest.raises(CleanupError, match="writers_stopped"):
            await migration.inspect_assets(db, cfg, source)
    with pytest.raises(CleanupError, match="invalid_migration_page"):
        await inspect(factory, cfg, source, limit=101)
    report = await inspect(factory, cfg, source)
    async with factory.begin() as db:
        (await db.get(User, owner)).role = UserRole.user
    with pytest.raises(CleanupError, match="admin_required"):
        await copy(factory, cfg, source, owner, report)
    async with factory.begin() as db:
        (await db.get(RuntimeControl, "global")).blocked = False
    with pytest.raises(CleanupError, match="generation_must_be_paused"):
        await verify(factory, cfg)
    assert not objects.calls
