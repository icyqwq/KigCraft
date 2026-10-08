"""Remote asset retirement I/O. Database reference/grace/receipt rules stay with callers."""
from contextlib import suppress
import re

from botocore.exceptions import ClientError
from sqlalchemy import select

from app.assets.repository import MAX_OBJECT_BYTES, MinioStore, StorageUnavailable, validate_bytes
from app.maintenance.orphan_assets import CleanupError, MAX_BATCH_BYTES
from app.maintenance.minio_bucket import target

MAX_VERSIONS = 100


class MissingObject(Exception):
    pass


class RemoteAssets:
    def __init__(self, settings):
        self.store = MinioStore(settings)
        self.identity = "minio:" + target(settings)

    def __str__(self):
        return self.identity

    def call(self, method, **arguments):
        try:
            return getattr(self.store.client, method)(Bucket=self.store.bucket, **arguments)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "NoSuchVersion"}:
                raise MissingObject() from None
            raise StorageUnavailable() from None
        except Exception:
            raise StorageUnavailable() from None

    def mode(self):
        value = self.call("get_bucket_versioning")
        mode = value.get("Status", "unversioned")
        if mode not in {"Enabled", "Suspended", "unversioned"} or value.get("MFADelete") == "Enabled":
            raise CleanupError("unsupported_bucket_versioning")
        return mode

    def read(self, key, sha, size, version=None):
        if type(size) is not int or not 0 < size <= MAX_OBJECT_BYTES:
            raise CleanupError("invalid_remote_asset_size")
        arguments = {"Key": key}
        if version is not None:
            arguments["VersionId"] = version
        response = self.call("get_object", **arguments)
        body = response.get("Body")
        try:
            if body is None or response.get("ContentLength") != size:
                raise CleanupError("remote_asset_content_changed")
            etag = response.get("ETag")
            if not isinstance(etag, str) or not re.fullmatch(r'"[a-fA-F0-9-]{1,100}"', etag):
                raise CleanupError("remote_asset_etag_unavailable")
            data = bytearray()
            while len(data) <= size:
                chunk = body.read(min(1024*1024, size + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            validate_bytes(data, sha, size)
            if version is not None and response.get("VersionId") != version:
                raise CleanupError("remote_version_changed")
            return {"version": response.get("VersionId"), "etag": etag}
        finally:
            if body is not None:
                with suppress(Exception):
                    body.close()

    def inspect(self, key, sha, size, *, missing=False):
        key = self.store.remote_key(key)
        mode = self.mode()
        if mode == "unversioned":
            try:
                current = self.read(key, sha, size)
                if current["version"] not in {None, "null"}:
                    raise CleanupError("bucket_versioning_changed")
                versions = [current]
            except MissingObject:
                if not missing:
                    raise CleanupError("remote_asset_missing") from None
                versions = []
            return {"mode": mode, "present": bool(versions), "versions": versions}
        response = self.call("list_object_versions", Prefix=key, MaxKeys=MAX_VERSIONS+1)
        if response.get("IsTruncated") is not False:
            raise CleanupError("remote_version_inventory_too_large")
        values = response.get("Versions", [])
        markers = response.get("DeleteMarkers", [])
        if not isinstance(values, list) or not isinstance(markers, list) or len(values)+len(markers)>MAX_VERSIONS:
            raise CleanupError("remote_version_inventory_too_large")
        values = [value for value in values if value.get("Key") == key]
        if size * len(values) > MAX_BATCH_BYTES:
            raise CleanupError("remote_version_bytes_too_large")
        versions = []
        for value in values:
            version = value.get("VersionId")
            if not isinstance(version, str) or not version or len(version) > 1024 or value.get("Size") != size:
                raise CleanupError("remote_version_changed")
            versions.append(self.read(key, sha, size, version))
        if len({value["version"] for value in versions}) != len(versions):
            raise CleanupError("duplicate_remote_version")
        if not missing:
            try:
                self.read(key, sha, size)  # A delete marker cannot be restored as a readable asset.
            except MissingObject:
                raise CleanupError("remote_asset_missing") from None
        return {"mode": mode, "present": bool(versions),
            "versions": sorted(versions, key=lambda item:item["version"]),
            "delete_markers_retained": len([value for value in markers if value.get("Key") == key])}

    def delete(self, key, sha, size):
        before = self.inspect(key, sha, size, missing=True)
        remote = self.store.remote_key(key)
        if self.mode() != before["mode"]:
            raise CleanupError("bucket_versioning_changed")
        for version in before["versions"]:
            arguments = {"Key": remote, "IfMatch": version["etag"]}
            if before["mode"] != "unversioned":
                arguments["VersionId"] = version["version"]
            # Stop all writers first. Some MinIO releases ignore If-Match on DELETE;
            # the maintenance lock/stop contract remains mandatory even with this
            # extra header. Never bypass object locks or fall back on an SDK error.
            try:
                self.call("delete_object", **arguments)
            except MissingObject:
                pass
        after = self.inspect(key, sha, size, missing=True)
        if after["mode"] != before["mode"] or after["present"]:
            raise CleanupError("remote_deletion_unconfirmed")
        # Retain delete-marker metadata: it contains no asset bytes and deleting a
        # mutable null marker could remove a concurrently replaced object.
        return after


def deployment_allowed(settings):
    local = settings.app_env in {"development", "test"} and settings.v2_runtime_mode == "local"
    if local:
        return settings.v2_enabled
    from app.codex_runtime.selection import runtime_allowed
    return (settings.v2_enabled and settings.app_env == "production"
            and settings.v2_runtime_mode == "container" and runtime_allowed(settings))


async def paused(db, settings, writers_stopped):
    from app.design.models import AdminAction, Operation, RuntimeControl
    from sqlalchemy import or_
    if not writers_stopped:
        raise CleanupError("writers_stopped_acknowledgement_required")
    if not deployment_allowed(settings):
        raise CleanupError("maintenance_configuration_required")
    control = await db.get(RuntimeControl, "global")
    if control is None or not control.blocked:
        raise CleanupError("generation_must_be_paused")
    if await db.scalar(select(Operation.id).where(or_(
            Operation.lease_owner.is_not(None), Operation.lease_expires_at.is_not(None))).limit(1)):
        raise CleanupError("worker_lease_present")
    if await db.scalar(select(AdminAction.id).where(AdminAction.status.in_(["pending","running"])).limit(1)):
        raise CleanupError("admin_action_pending")
