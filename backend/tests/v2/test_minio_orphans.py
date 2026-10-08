"""Fake object service and throwaway PostgreSQL only; never uses actual MinIO."""
from datetime import timedelta
import hashlib
from uuid import uuid4

from botocore.stub import Stubber
import pytest

from app.admin.v2_controls import set_control
from app.assets import repository
from app.auth.models import UserRole
from app.core.time import utc_now
from app.db.models import User
from app.design.models import Asset, InputRecord
from app.maintenance import minio_orphans as service
from app.maintenance.orphan_assets import CleanupError
from app.maintenance.remote_assets import RemoteAssets
from .test_minio_assets import config
from .test_remote_retention import Objects
from .test_operations import factory, setup, quoted, submit_request
from .test_migrations import migration_database


class Orphans(Objects):
    def __init__(self, mode, key, now):
        super().__init__(mode, key, b"orphan image fixture")
        self.modified = now - timedelta(days=60)
        self.hidden = False
        self.newest = now
    def list_objects_v2(self, **args):
        self.calls.append(("page", args))
        assert args["Prefix"] == "v2/"
        return {"IsTruncated": False, "Contents": [{"Key": self.key}] if self.versions else []}
    def list_object_versions(self, **args):
        if args["Prefix"] == "v2/":
            args = {**args, "Prefix": self.key}
        result = super().list_object_versions(**args)
        for row in result["Versions"]:
            row["LastModified"] = self.modified
        return result
    def get_object(self, **args):
        if self.hidden and "VersionId" not in args:
            from .test_remote_retention import missing
            raise missing()
        result = super().get_object(**args)
        version = args.get("VersionId", next(reversed(self.versions), None))
        result["Metadata"] = {"sha256": hashlib.sha256(self.versions[version]).hexdigest()}
        result["LastModified"] = self.modified
        return result


async def prepared(factory, tmp_path, monkeypatch, mode="unversioned"):
    owner, design, branch = await setup(factory)
    cfg = config(tmp_path)
    cfg.v2_enabled = True
    cfg.app_env = "test"
    cfg.v2_runtime_mode = "local"
    cfg.v2_orphan_retention_days = 30
    cfg.v2_orphan_grace_days = 7
    now = utc_now()
    objects = Orphans(mode, f"v2/uploads/{uuid4()}.png", now)
    monkeypatch.setattr(repository, "make_client", lambda _: objects)
    async with factory.begin() as db:
        (await db.get(User, owner)).role = UserRole.admin
        await set_control(db, "global", True)
    return cfg, owner, branch, now, objects


async def inspect(factory, cfg, now, **kwargs):
    async with factory.begin() as db:
        return await service.inspect_orphans(db, cfg, now=now, writers_stopped=True, **kwargs)


async def retire(factory, cfg, owner, now, report):
    async with factory.begin() as db:
        return await service.retire_orphans(db, cfg, report["digest"], owner, "Reviewed orphan policy",
            now=now, writers_stopped=True)


async def purge(factory, cfg, owner, now, retired):
    async with factory.begin() as db:
        return await service.purge_orphans(db, cfg, retired["receipt_id"], retired["manifest"]["digest"],
            owner, "Reviewed orphan deletion", now=now, writers_stopped=True)


@pytest.mark.parametrize("mode", ["unversioned", "Enabled", "Suspended"])
async def test_retire_grace_purge_and_idempotency(factory, tmp_path, monkeypatch, mode):
    cfg, owner, _, now, objects = await prepared(factory, tmp_path, monkeypatch, mode)
    report = await inspect(factory, cfg, now)
    assert len(report["candidates"]) == 1
    retired = await retire(factory, cfg, owner, now, report)
    assert await retire(factory, cfg, owner, now, report) == retired
    assert objects.versions
    with pytest.raises(CleanupError, match="orphan_grace_period"):
        await purge(factory, cfg, owner, now + timedelta(days=1), retired)
    later = now + timedelta(days=8)
    result = await purge(factory, cfg, owner, later, retired)
    assert result["count"] == 1 and not objects.versions
    assert await purge(factory, cfg, owner, later, retired) == result


async def test_hidden_old_versions_are_discovered_and_markers_retained(factory, tmp_path, monkeypatch):
    cfg, owner, _, now, objects = await prepared(factory, tmp_path, monkeypatch, "Enabled")
    objects.hidden = True
    objects.markers = [{"Key": objects.key, "VersionId": "marker", "LastModified": objects.modified}]
    report = await inspect(factory, cfg, now)
    assert len(report["candidates"][0]["object"]["versions"]) == 2
    retired = await retire(factory, cfg, owner, now, report)
    await purge(factory, cfg, owner, now + timedelta(days=8), retired)
    assert not objects.versions and objects.markers
    assert not (await inspect(factory, cfg, now))["candidates"]


@pytest.mark.parametrize("kind", ["reference", "registered"])
async def test_new_database_reference_prevents_deletion(factory, tmp_path, monkeypatch, kind):
    cfg, owner, branch, now, objects = await prepared(factory, tmp_path, monkeypatch)
    report = await inspect(factory, cfg, now)
    retired = await retire(factory, cfg, owner, now, report)
    item = report["candidates"][0]
    async with factory.begin() as db:
        if kind == "reference":
            db.add(InputRecord(branch_id=branch, digest="b"*64, snapshot={"orphan": item["key"]}))
        else:
            identity = item["key"].split("/")[-1].split(".")[0]
            db.add(Asset(id=identity, owner_id=owner, kind="reference", object_key=item["key"],
                sha256=item["sha256"], byte_size=item["bytes"], mime_type="image/png",
                width=1, height=1, status="purged"))
    assert not (await inspect(factory, cfg, now))["candidates"]
    with pytest.raises(CleanupError, match="orphan_became_referenced"):
        await purge(factory, cfg, owner, now + timedelta(days=8), retired)
    assert not any(method == "delete" for method, _ in objects.calls)


@pytest.mark.parametrize("mode", ["unversioned", "Enabled", "Suspended"])
async def test_identical_replacement_is_not_a_retired_object(factory, tmp_path, monkeypatch, mode):
    cfg, owner, _, now, objects = await prepared(factory, tmp_path, monkeypatch, mode)
    retired = await retire(factory, cfg, owner, now, await inspect(factory, cfg, now))
    if mode == "Enabled":
        objects.versions["replacement"] = next(iter(objects.versions.values()))
    else:
        objects.modified = now + timedelta(days=1)
    with pytest.raises(CleanupError, match="orphan_(object|versions)_changed"):
        await purge(factory, cfg, owner, now + timedelta(days=8), retired)
    assert not any(method == "delete" for method, _ in objects.calls)


async def test_partial_deletion_retry_reuses_committed_manifest(factory, tmp_path, monkeypatch):
    cfg, owner, _, now, objects = await prepared(factory, tmp_path, monkeypatch, "Enabled")
    retired = await retire(factory, cfg, owner, now, await inspect(factory, cfg, now))
    objects.fail_after_delete = True
    with pytest.raises(repository.StorageUnavailable):
        await purge(factory, cfg, owner, now + timedelta(days=8), retired)
    assert len(objects.versions) == 1
    assert (await purge(factory, cfg, owner, now + timedelta(days=8), retired))["count"] == 1
    assert not objects.versions


async def test_scope_original_grace_and_commit_are_bound(factory, tmp_path, monkeypatch):
    cfg, owner, _, now, objects = await prepared(factory, tmp_path, monkeypatch)
    cfg.v2_orphan_grace_days = 14
    report = await inspect(factory, cfg, now)
    async with factory.begin() as db:
        retired = await service.retire_orphans(db, cfg, report["digest"], owner, "Review",
            now=now, writers_stopped=True)
        with pytest.raises(CleanupError, match="retirement_commit_required"):
            await service.purge_orphans(db, cfg, retired["receipt_id"], report["digest"], owner,
                "Review deletion", now=now + timedelta(days=20), writers_stopped=True)
    cfg.v2_orphan_grace_days = 1
    with pytest.raises(CleanupError, match="orphan_grace_period"):
        await purge(factory, cfg, owner, now + timedelta(days=8), retired)
    cfg.v2_s3_prefix = "another"
    with pytest.raises(CleanupError, match="retirement_scope_changed"):
        await purge(factory, cfg, owner, now + timedelta(days=20), retired)
    assert objects.versions


async def test_disabled_pause_stop_and_unconfirmed_operation(factory, tmp_path, monkeypatch):
    cfg, owner, branch, now, objects = await prepared(factory, tmp_path, monkeypatch)
    cfg.v2_orphan_retention_days = 0
    with pytest.raises(CleanupError, match="disabled"):
        await inspect(factory, cfg, now)
    cfg.v2_orphan_retention_days = 30
    async with factory.begin() as db:
        with pytest.raises(CleanupError, match="writers_stopped"):
            await service.inspect_orphans(db, cfg)
    async with factory.begin() as db:
        await set_control(db, "global", False)
    with pytest.raises(CleanupError, match="generation_must_be_paused"):
        await inspect(factory, cfg, now)
    payload = await quoted(factory, owner, branch)
    await submit_request(factory, owner, branch, payload)
    async with factory.begin() as db:
        await set_control(db, "global", True)
    with pytest.raises(CleanupError, match="operation_unconfirmed"):
        await inspect(factory, cfg, now)
    assert not objects.calls


async def test_recent_unsupported_and_digest_change(factory, tmp_path, monkeypatch):
    cfg, owner, _, now, objects = await prepared(factory, tmp_path, monkeypatch)
    report = await inspect(factory, cfg, now)
    objects.modified = now
    assert not (await inspect(factory, cfg, now))["candidates"]
    with pytest.raises(CleanupError, match="inventory_changed"):
        await retire(factory, cfg, owner, now, report)
    objects.key = "v2/unknown/file.png"
    assert (await inspect(factory, cfg, now))["retained"][0]["reason"] == "unsupported_or_referenced"


def test_paginated_version_listing_uses_sdk_key_and_version_markers(tmp_path):
    cfg = config(tmp_path)
    remote = RemoteAssets(cfg)
    key = "v2/uploads/12345678-1234-1234-1234-123456789abc.png"
    with Stubber(remote.store.client) as stub:
        stub.add_response("get_bucket_versioning", {"Status":"Enabled"}, {"Bucket":remote.store.bucket})
        stub.add_response("list_object_versions",
            {"IsTruncated":True, "Versions":[{"Key":key,"VersionId":"v1"}],
             "NextKeyMarker":key, "NextVersionIdMarker":"v1"},
            {"Bucket":remote.store.bucket,"Prefix":"v2/","MaxKeys":1})
        mode, keys, cursor = service.page(remote, None, 1)
        assert keys == [key[3:]]
        stub.add_response("get_bucket_versioning", {"Status":"Enabled"}, {"Bucket":remote.store.bucket})
        stub.add_response("list_object_versions", {"IsTruncated":False,"Versions":[{"Key":key,"VersionId":"v2"}]},
            {"Bucket":remote.store.bucket,"Prefix":"v2/","MaxKeys":1,"KeyMarker":key,"VersionIdMarker":"v1"})
        assert service.page(remote, cursor, 1)[2] is None
        stub.assert_no_pending_responses()


async def test_missing_metadata_and_corrupt_historical_bytes_refuse(factory, tmp_path, monkeypatch):
    cfg, owner, _, now, objects = await prepared(factory, tmp_path, monkeypatch, "Enabled")
    original = objects.get_object
    def missing_metadata(**args):
        result = original(**args)
        result["Metadata"] = {}
        return result
    monkeypatch.setattr(objects, "get_object", missing_metadata)
    with pytest.raises(CleanupError, match="orphan_metadata_unavailable"):
        await inspect(factory, cfg, now)
    monkeypatch.setattr(objects, "get_object", original)
    objects.versions["current"] = b"different object bytes"
    with pytest.raises((CleanupError, repository.StorageUnavailable)):
        await inspect(factory, cfg, now)
    assert not any(method == "delete" for method, _ in objects.calls)


async def test_non_admin_cannot_retire_or_delete(factory, tmp_path, monkeypatch):
    cfg, owner, _, now, objects = await prepared(factory, tmp_path, monkeypatch)
    report = await inspect(factory, cfg, now)
    async with factory.begin() as db:
        (await db.get(User, owner)).is_banned = True
    with pytest.raises(CleanupError, match="admin_required"):
        await retire(factory, cfg, owner, now, report)
    assert objects.versions


def test_unversioned_pagination_sdk_and_cursor_scope(tmp_path):
    cfg = config(tmp_path)
    remote = RemoteAssets(cfg)
    key = "v2/uploads/12345678-1234-1234-1234-123456789abc.png"
    with Stubber(remote.store.client) as stub:
        stub.add_response("get_bucket_versioning", {}, {"Bucket":remote.store.bucket})
        stub.add_response("list_objects_v2",
            {"IsTruncated":True, "Contents":[{"Key":key}]},
            {"Bucket":remote.store.bucket,"Prefix":"v2/","MaxKeys":1})
        _, _, cursor = service.page(remote, None, 1)
        stub.add_response("get_bucket_versioning", {}, {"Bucket":remote.store.bucket})
        stub.add_response("list_objects_v2", {"IsTruncated":False},
            {"Bucket":remote.store.bucket,"Prefix":"v2/","MaxKeys":1,"StartAfter":key})
        assert service.page(remote, cursor, 1) == ("unversioned", [], None)
        stub.add_response("get_bucket_versioning", {}, {"Bucket":remote.store.bucket})
        with pytest.raises(CleanupError, match="invalid_orphan_cursor"):
            service.page(remote, {**cursor, "key":"foreign/key"}, 1)
        stub.assert_no_pending_responses()
