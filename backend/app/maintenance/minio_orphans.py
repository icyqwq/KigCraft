"""Review and reclaim unregistered MinIO bytes after a committed grace period."""
import argparse
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import JSON, String, Text, and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assets.repository import MAX_OBJECT_BYTES, storage_io
from app.core.config import Settings
from app.core.time import utc_now
from app.db.base import Base
from app.db.v2_schema import require_v2_schema
from app.design.models import AdminAction, Asset, Operation
from app.maintenance.orphan_assets import CleanupError, MAX_BATCH_BYTES, OBJECT, database_identity, digest
from app.maintenance.remote_assets import RemoteAssets, MAX_VERSIONS, paused
from app.maintenance.runtime_retention import administrator, receipt
from app.maintenance.upload_retention import lock_registry
from app.operations.service import TERMINAL

SCOPE = "v2_minio_orphans"
MAX_PAGE = 25


def timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CleanupError("object_age_unavailable")
    return value.astimezone(timezone.utc)


def page(remote, after, limit):
    """Version listing includes data hidden by delete markers."""
    prefix = remote.store.prefix + "/"
    mode = remote.mode()
    args = {"Prefix": prefix, "MaxKeys": limit}
    if after is not None:
        if (not isinstance(after, dict) or set(after) != {"mode", "key", "version"}
                or after["mode"] != mode or not isinstance(after["key"], str)
                or not after["key"].startswith(prefix) or len(after["key"]) > 2048
                or not isinstance(after["version"], str) or len(after["version"]) > 1024):
            raise CleanupError("invalid_orphan_cursor")
        args["StartAfter" if mode == "unversioned" else "KeyMarker"] = after["key"]
        if mode != "unversioned" and after["version"]:
            args["VersionIdMarker"] = after["version"]
    response = remote.call("list_objects_v2" if mode == "unversioned" else "list_object_versions", **args)
    rows = response.get("Contents", []) if mode == "unversioned" else (
        response.get("Versions", []) + response.get("DeleteMarkers", []))
    if not isinstance(rows, list) or len(rows) > limit or type(response.get("IsTruncated")) is not bool:
        raise CleanupError("invalid_remote_inventory")
    keys = []
    for row in rows:
        key = row.get("Key")
        if not isinstance(key, str) or not key.startswith(prefix):
            raise CleanupError("invalid_remote_inventory")
        keys.append(key[len(prefix):])
    cursor = None
    if response["IsTruncated"]:
        key = rows[-1]["Key"] if mode == "unversioned" and rows else response.get("NextKeyMarker")
        version = "" if mode == "unversioned" else response.get("NextVersionIdMarker", "")
        cursor = {"mode": mode, "key": key, "version": version}
        if (not isinstance(key, str) or not key.startswith(prefix) or cursor == after
                or not isinstance(version, str) or not rows):
            raise CleanupError("invalid_remote_cursor")
    return mode, sorted(set(keys)), cursor


def object_details(remote, key, mode):
    """Read metadata and verify every byte-bearing version before retirement."""
    full = remote.store.remote_key(key)
    version = None
    dates = []
    if mode != "unversioned":
        response = remote.call("list_object_versions", Prefix=full, MaxKeys=MAX_VERSIONS + 1)
        rows = response.get("Versions", [])
        markers = response.get("DeleteMarkers", [])
        if response.get("IsTruncated") is not False or len(rows) + len(markers) > MAX_VERSIONS:
            raise CleanupError("remote_version_inventory_too_large")
        rows = [r for r in rows if r.get("Key") == full]
        if not rows:
            return None  # A marker alone contains no image bytes.
        dates = [timestamp(r.get("LastModified")) for r in rows + [
            r for r in markers if r.get("Key") == full]]
        version = rows[0]["VersionId"]
    args = {"Key": full}
    if version is not None:
        args["VersionId"] = version
    response = remote.call("get_object", **args)
    body = response.get("Body")
    try:
        sha = response.get("Metadata", {}).get("sha256")
        size = response.get("ContentLength")
        dates.append(timestamp(response.get("LastModified")))
        if (not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha)
                or type(size) is not int or not 0 < size <= MAX_OBJECT_BYTES):
            raise CleanupError("orphan_metadata_unavailable")
    finally:
        if body is not None:
            body.close()
    observed = remote.inspect(key, sha, size, missing=mode != "unversioned")
    if observed["mode"] != mode:
        raise CleanupError("bucket_versioning_changed")
    return {"key": key, "sha256": sha, "bytes": size,
            "modified_at": max(dates).isoformat(), "object": observed}


async def protected(db, key):
    identity = key.split("/")[-1].split(".")[0]
    # All asset states and all opaque textual/JSON references protect bytes.
    if await db.scalar(select(Asset.id).where(or_(
            func.lower(Asset.id) == identity, func.lower(Asset.object_key) == key)).limit(1)):
        return True
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        if table.name in {Asset.__tablename__, AdminAction.__tablename__}:
            continue
        columns = [c for c in table.columns if isinstance(c.type, (String, Text, JSON))]
        matches = [or_(func.strpos(func.lower(cast(c, Text)), identity) > 0,
                       func.strpos(func.lower(cast(c, Text)), key) > 0) for c in columns]
        if matches and await db.scalar(select(1).select_from(table).where(or_(*matches)).limit(1)):
            return True
    return False


async def guard(db, settings, writers_stopped):
    if settings.v2_asset_backend != "minio" or settings.v2_orphan_retention_days == 0:
        raise CleanupError("minio_orphan_retention_disabled")
    await lock_registry(db)
    await paused(db, settings, writers_stopped)
    if await db.scalar(select(Operation.id).where(or_(
        Operation.state.not_in(TERMINAL), Operation.reservation_closed.is_(False),
        and_(Operation.dispatch_intent_at.is_not(None), or_(Operation.upstream_status.is_(None),
            Operation.upstream_status.not_in(["completed", "failed", "interrupted"]))),
        Operation.context_allocation["state"].as_string().in_(["sent", "unknown"]),
    )).limit(1)):
        raise CleanupError("operation_unconfirmed")
    return RemoteAssets(settings)


async def inspect_locked(db, remote, settings, *, now, after=None, limit=MAX_PAGE):
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise CleanupError("invalid_orphan_limit")
    mode, keys, cursor = await storage_io(page, remote, after, limit)
    cutoff = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=settings.v2_orphan_retention_days)
    candidates, retained = [], []
    total = 0
    for key in keys:
        if not OBJECT.fullmatch(key) or await protected(db, key):
            retained.append({"key": key, "reason": "unsupported_or_referenced"})
            continue
        item = await storage_io(object_details, remote, key, mode)
        if item is None or datetime.fromisoformat(item["modified_at"]) >= cutoff:
            retained.append({"key": key, "reason": "marker_only_or_recent"})
            continue
        total += item["bytes"] * len(item["object"]["versions"])
        if total > MAX_BATCH_BYTES:
            raise CleanupError("orphan_batch_too_large")
        candidates.append(item)
    report = {"format": 1, "scope": SCOPE, "database": await database_identity(db),
        "storage_root": str(remote), "mode": mode, "after": after, "limit": limit, "next_after": cursor,
        "cutoff": cutoff.isoformat(), "retention_days": settings.v2_orphan_retention_days,
        "grace_days": settings.v2_orphan_grace_days, "candidates": candidates, "retained": retained}
    report["digest"] = digest(report)
    return report


async def inspect_orphans(db, settings, *, writers_stopped=False, now=None, **kwargs):
    remote = await guard(db, settings, writers_stopped)
    return await inspect_locked(db, remote, settings, now=now or utc_now(), **kwargs)


async def retire_orphans(db, settings, expected_digest, actor_id, reason, *,
                         writers_stopped=False, now=None, **kwargs):
    remote = await guard(db, settings, writers_stopped)
    await administrator(db, actor_id, reason)
    now = now or utc_now()
    request = digest({"action": "retire_minio_orphans", "digest": expected_digest, "reason": reason.strip()})
    old = await db.scalar(select(AdminAction).where(
        AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
    if old:
        return {"receipt_id": old.id, **old.result}
    report = await inspect_locked(db, remote, settings, now=now, **kwargs)
    if report["digest"] != expected_digest:
        raise CleanupError("inventory_changed")
    return await receipt(db, actor_id, reason, "retire_minio_orphans", request,
        {"manifest": report, "count": len(report["candidates"]), "status": "retired"}, now)


async def purge_orphans(db, settings, batch_id, expected_digest, actor_id, reason, *,
                        writers_stopped=False, now=None):
    remote = await guard(db, settings, writers_stopped)
    await administrator(db, actor_id, reason)
    now = now or utc_now()
    source = await db.get(AdminAction, batch_id)
    if source is None or source.action != "retire_minio_orphans" or source.status != "completed":
        raise CleanupError("retirement_receipt_required")
    report = source.result.get("manifest", {})
    if (report.get("scope") != SCOPE or report.get("format") != 1
            or report.get("digest") != digest({k:v for k,v in report.items() if k != "digest"})
            or report.get("digest") != expected_digest):
        raise CleanupError("invalid_retirement_manifest")
    if report["database"] != await database_identity(db) or report["storage_root"] != str(remote):
        raise CleanupError("retirement_scope_changed")
    if await db.scalar(text("SELECT xmin::text::bigint = txid_current() % 4294967296 FROM v2_admin_actions WHERE id=:id"),
                       {"id": batch_id}):
        raise CleanupError("retirement_commit_required")
    if source.completed_at is None or now < source.completed_at + timedelta(
            days=max(report["grace_days"], settings.v2_orphan_grace_days)):
        raise CleanupError("orphan_grace_period")
    request = digest({"action": "purge_minio_orphans", "batch": batch_id,
                      "digest": expected_digest, "reason": reason.strip()})
    old = await db.scalar(select(AdminAction).where(
        AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
    if old:
        return {"receipt_id": old.id, **old.result}
    items = report["candidates"]
    if len(items) > MAX_PAGE or len({i["key"] for i in items}) != len(items):
        raise CleanupError("invalid_retirement_manifest")
    # Whole-batch preflight. After a partial deletion only a subset of the exact
    # retired versions may remain. Newly created identical bytes still get protected.
    for item in items:
        key = item["key"]
        if not OBJECT.fullmatch(key) or await protected(db, key):
            raise CleanupError("orphan_became_referenced")
        observed = await storage_io(remote.inspect, key, item["sha256"], item["bytes"], missing=True)
        if (observed["mode"] != report["mode"] or any(
                value not in item["object"]["versions"] for value in observed["versions"])):
            raise CleanupError("orphan_versions_changed")
        if observed["present"] and report["mode"] == "unversioned":
            current = await storage_io(object_details, remote, key, report["mode"])
            if current != item:
                raise CleanupError("orphan_object_changed")
        # Suspended buckets have mutable null versions, so check their age too.
        elif any(v["version"] == "null" for v in observed["versions"]):
            current = await storage_io(object_details, remote, key, report["mode"])
            if datetime.fromisoformat(current["modified_at"]) > datetime.fromisoformat(item["modified_at"]):
                raise CleanupError("orphan_object_changed")
    for item in items:
        await storage_io(remote.delete, item["key"], item["sha256"], item["bytes"])
    return await receipt(db, actor_id, reason, "purge_minio_orphans", request,
        {"status": "purged", "batch_id": batch_id, "manifest_digest": expected_digest, "count": len(items)}, now)


async def run(settings, args):
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as db:
            common = {"writers_stopped": args.writers_stopped}
            if args.command == "purge":
                return await purge_orphans(db, settings, args.batch_id, args.expected_digest,
                    args.actor_id, args.reason, **common)
            common.update(after=json.loads(args.after) if args.after else None, limit=args.limit)
            if args.command == "inspect":
                return await inspect_orphans(db, settings, **common)
            return await retire_orphans(db, settings, args.expected_digest, args.actor_id, args.reason, **common)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "retire", "purge"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--after", help="JSON next_after cursor from the preceding page")
    parser.add_argument("--limit", type=int, default=MAX_PAGE)
    parser.add_argument("--batch-id")
    parser.add_argument("--expected-digest")
    parser.add_argument("--actor-id")
    parser.add_argument("--reason")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise CleanupError("configuration_file_missing")
        result = asyncio.run(run(Settings(_env_file=args.env_file), args))
    except Exception as exc:
        print(json.dumps({"status": "refused", "code": str(exc) if isinstance(exc, CleanupError) else "maintenance_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
