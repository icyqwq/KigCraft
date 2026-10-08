"""Explicit, non-destructive migration of registered file assets to MinIO."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assets.repository import MAX_OBJECT_BYTES, MinioStore, storage_io, validate_bytes
from app.core.config import Settings
from app.core.time import utc_now
from app.db.v2_schema import require_v2_schema
from app.design.models import AdminAction, Asset, Operation, RuntimeControl
from app.maintenance.orphan_assets import (
    CleanupError, MAX_BATCH_BYTES, checked, database_identity, digest, fingerprint, root_path,
)
from app.maintenance.runtime_retention import administrator, receipt
from app.maintenance.upload_retention import lock_registry

SCOPE = "v2_file_assets_to_minio"
MAX_CATALOG = 100000
ACTION = "migrate_minio_assets"


async def guard(db, settings, writers_stopped):
    if not settings.v2_enabled or settings.v2_asset_backend != "minio":
        raise CleanupError("minio_configuration_required")
    if not writers_stopped:
        raise CleanupError("writers_stopped_acknowledgement_required")
    await lock_registry(db)
    control = await db.get(RuntimeControl, "global")
    if control is None or not control.blocked:
        raise CleanupError("generation_must_be_paused")
    if await db.scalar(select(Operation.id).where(or_(
        Operation.lease_owner.is_not(None), Operation.lease_expires_at.is_not(None),
    )).limit(1)):
        raise CleanupError("worker_lease_present")
    if await db.scalar(select(AdminAction.id).where(
        AdminAction.status.in_(["pending", "running"])).limit(1)):
        raise CleanupError("admin_action_pending")


def target_identity(settings):
    # Credentials deliberately excluded: rotation doesn't change object identity.
    return digest({"endpoint": settings.v2_s3_endpoint_url,
        "bucket": settings.v2_s3_bucket, "prefix": settings.v2_s3_prefix,
        "region": settings.v2_s3_region})


def source_identity(root):
    info = root.stat()
    return digest({"path": str(root), "device": info.st_dev, "inode": info.st_ino})


def page_options(after, limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise CleanupError("invalid_migration_page")
    if after is not None:
        try:
            if str(UUID(after)) != after:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise CleanupError("invalid_migration_page") from None


def descriptor(row):
    if row.status not in {"ready", "expired", "purged"}:
        raise CleanupError("unknown_asset_status")
    if (not 0 < row.byte_size <= MAX_OBJECT_BYTES
            or len(row.sha256) != 64
            or any(c not in "0123456789abcdef" for c in row.sha256)):
        raise CleanupError("invalid_asset_identity")
    return {"asset_id": row.id, "owner_id": row.owner_id, "key": row.object_key,
        "sha256": row.sha256, "bytes": row.byte_size, "mime": row.mime_type,
        "status": row.status}


def source_file(root, item):
    path = checked(root, item["key"])
    if path.stat(follow_symlinks=False).st_nlink != 1:
        raise CleanupError("linked_source_file")
    if fingerprint(path) != {"sha256": item["sha256"], "bytes": item["bytes"]}:
        raise CleanupError("source_identity_mismatch")
    return path


async def inventory(db, settings, source, repo, *, after=None, limit=25):
    page_options(after, limit)
    root = root_path(source)
    query = select(Asset).order_by(Asset.id)
    if after:
        query = query.where(Asset.id > after)
    rows = list(await db.scalars(query.limit(limit + 1)))
    more, rows = len(rows) > limit, rows[:limit]
    candidates, purged, total = [], [], 0
    for row in rows:
        item = descriptor(row)
        repo.remote_key(item["key"])
        if row.status == "purged":
            purged.append(item)
            continue
        total += item["bytes"]
        if total > MAX_BATCH_BYTES:
            raise CleanupError("migration_batch_too_large_reduce_limit")
        await storage_io(source_file, root, item)
        candidates.append(item)
    report = {"format": 1, "scope": SCOPE, "database": await database_identity(db),
        "source": source_identity(root), "target": target_identity(settings),
        "after": after, "limit": limit, "next_after": rows[-1].id if more else None,
        "candidates": candidates, "purged": purged, "bytes": total}
    report["digest"] = digest(report)
    return root, report


async def inspect_assets(db, settings, source, *, writers_stopped=False, after=None, limit=25):
    await guard(db, settings, writers_stopped)
    repo = MinioStore(settings)
    _, report = await inventory(db, settings, source, repo, after=after, limit=limit)
    return report


def copy_file(root, repo, item):
    path = source_file(root, item)
    with path.open("rb") as stream:
        data = stream.read(MAX_OBJECT_BYTES + 1)
    validate_bytes(data, item["sha256"], item["bytes"])
    repo.put(item["key"], data, item["sha256"], item["mime"])
    source_file(root, item)


async def copy_assets(db, settings, source, expected_digest, actor_id, reason, *,
                      writers_stopped=False, after=None, limit=25):
    await guard(db, settings, writers_stopped)
    await administrator(db, actor_id, reason)
    repo = MinioStore(settings)
    root, report = await inventory(db, settings, source, repo, after=after, limit=limit)
    if not expected_digest or expected_digest != report["digest"]:
        raise CleanupError("inventory_changed")
    request = digest({"action": ACTION, "digest": expected_digest, "reason": reason.strip()})
    existing = await db.scalar(select(AdminAction).where(
        AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
    if existing:
        # A receipt proves the past copy, not present remote availability.
        return {"receipt_id": existing.id, **existing.result}
    for item in report["candidates"]:
        # Conditional writes plus full readback make partial-failure retries safe.
        # storage_io drains cancelled I/O before the transaction releases locks.
        await storage_io(copy_file, root, repo, item)
    _, current = await inventory(db, settings, root, repo, after=after, limit=limit)
    if current != report:
        raise CleanupError("inventory_changed")
    return await receipt(db, actor_id, reason, ACTION, request,
        {"status": "copied", "count": len(report["candidates"]), "manifest": report,
         "configuration_changed": False, "source_removed": False}, utc_now())


async def verify_assets(db, settings, *, writers_stopped=False):
    """Full catalog verification, independent of page receipts and local cache."""
    await guard(db, settings, writers_stopped)
    repo = MinioStore(settings)
    rows = list(await db.scalars(select(Asset).order_by(Asset.id).limit(MAX_CATALOG + 1)))
    if len(rows) > MAX_CATALOG:
        raise CleanupError("migration_catalog_too_large")
    catalog = hashlib.sha256()
    count = total = purged = 0
    for row in rows:
        item = descriptor(row)
        repo.remote_key(item["key"])
        catalog.update((digest(item) + "\n").encode("ascii"))
        if row.status == "purged":
            purged += 1
            continue
        await storage_io(repo.read, item["key"], item["sha256"], item["bytes"])
        count += 1
        total += item["bytes"]
    result = {"format": 1, "scope": SCOPE, "status": "verified",
        "database": await database_identity(db), "target": target_identity(settings),
        "catalog_digest": catalog.hexdigest(), "verified_count": count,
        "verified_bytes": total, "purged_count": purged,
        "configuration_changed": False, "source_removed": False}
    result["digest"] = digest(result)
    return result


async def run(settings, args):
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            if args.command == "verify":
                return await verify_assets(db, settings, writers_stopped=args.writers_stopped)
            if args.source is None:
                raise CleanupError("source_required")
            options = {"writers_stopped": args.writers_stopped, "after": args.after, "limit": args.limit}
            if args.command == "inspect":
                return await inspect_assets(db, settings, args.source, **options)
            return await copy_assets(db, settings, args.source, args.expected_digest,
                args.actor_id, args.reason, **options)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "copy", "verify"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--after")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--expected-digest")
    parser.add_argument("--actor-id")
    parser.add_argument("--reason")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise CleanupError("configuration_file_missing")
        result = asyncio.run(run(Settings(_env_file=args.env_file), args))
    except Exception as exc:
        print(json.dumps({"status": "refused", "code": str(exc) if isinstance(exc, CleanupError)
                          else "minio_migration_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
