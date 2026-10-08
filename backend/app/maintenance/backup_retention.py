"""Explicit, default-off retention for complete local or container backup bundles.

A committed retirement receipt precedes every unlink. No recursive deletion and
no automatic schedule. Backup creation/restoration/readers must be stopped.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import Settings
from app.core.time import utc_now
from app.db.v2_schema import require_v2_schema
from app.design.models import AdminAction
from app.maintenance import backup_bundle as backup
from app.maintenance.codex_snapshot import SnapshotError
from app.maintenance.orphan_assets import CleanupError, checked, database_identity, digest, maintenance_lock, root_path
from app.maintenance.runtime_retention import administrator, receipt

SCOPE = "v2_local_backup_retention"
CONTAINER_SCOPE = "v2_container_backup_retention"


def bundle_scope(settings):
    return backup.CONTAINER_SCOPE if settings.v2_runtime_mode == "container" else backup.SCOPE


def retention_scope(settings):
    return CONTAINER_SCOPE if settings.v2_runtime_mode == "container" else SCOPE
MAX_BUNDLES = 100
MAX_FILES = 100000
MAX_BYTES = 64 * 1024**3


def policy(settings):
    if not settings.v2_backup_retention_days:
        raise CleanupError("backup_retention_disabled")
    return {"days": settings.v2_backup_retention_days, "minimum": settings.v2_backup_min_copies}


def directory_id(path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or path.is_junction():
        raise CleanupError("unsafe_backup_directory")
    return [info.st_dev, info.st_ino]


def regular(path):
    info = path.stat(follow_symlinks=False)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink()
            or path.is_junction() or info.st_size > backup.MAX_FILE_BYTES):
        raise CleanupError("unsafe_backup_file")
    value = {"bytes": info.st_size, "sha256": backup.file_digest(path), "mtime_ns": info.st_mtime_ns}
    after = path.stat(follow_symlinks=False)
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise CleanupError("backup_file_changed")
    return value


def inventory(root):
    result = {}
    directories = []
    stack = [root]
    total = 0
    while stack:
        parent = stack.pop()
        for path in parent.iterdir():
            key = path.relative_to(root).as_posix()
            checked(root, key)
            if path.is_dir():
                directory_id(path)
                directories.append(key)
                if len(directories) > MAX_FILES:
                    raise CleanupError("backup_inventory_too_large")
                stack.append(path)
            else:
                if len(result) >= MAX_FILES:
                    raise CleanupError("backup_inventory_too_large")
                value = regular(path)
                total += value["bytes"]
                if total > MAX_BYTES:
                    raise CleanupError("backup_inventory_too_large")
                result[key] = value
    return {"files": result, "directories": sorted(directories)}


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise CleanupError("invalid_backup_timestamp") from None


async def guard(db, settings, root, stopped):
    if not stopped:
        raise CleanupError("backup_users_stopped_acknowledgement_required")
    from app.codex_runtime.selection import runtime_allowed
    local = settings.app_env in {"development", "test"} and settings.v2_runtime_mode == "local"
    production = settings.app_env == "production" and settings.v2_runtime_mode == "container" and runtime_allowed(settings)
    if not settings.v2_enabled or not (local or production):
        raise CleanupError("backup_configuration_required")
    selected = policy(settings)
    root = root_path(root)
    if root.parent == root:
        raise CleanupError("dedicated_backup_root_required")
    for value in (settings.v2_storage_dir, settings.v2_container_root if production else settings.v2_runtime_dir):
        source = Path(value).resolve()
        if root.is_relative_to(source) or source.is_relative_to(root):
            raise CleanupError("backup_source_overlap")
    await maintenance_lock(db)
    return root, selected


async def inspect_backups(db, settings, root, *, users_stopped=False, now=None):
    root, selected = await guard(db, settings, root, users_stopped)
    now = (now or utc_now()).astimezone(timezone.utc)
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=selected["days"])
    identity = await database_identity(db)
    entries = list(root.iterdir())
    if len(entries) > MAX_BUNDLES:
        raise CleanupError("backup_catalog_too_large")
    bundles, protected = [], []
    total_files = total_bytes = 0
    for path in sorted(entries):
        name = path.name
        try:
            checked(root, name)
            inode = directory_id(path)
            manifest = backup.verify_bundle(path)
            if manifest["scope"] != bundle_scope(settings):
                raise CleanupError("different_backup_scope")
            if manifest["database_identity"] != identity:
                raise CleanupError("different_database")
            created = timestamp(manifest["created_at"])
            if created > now:
                raise CleanupError("future_backup_timestamp")
            tree = inventory(path)
        except (CleanupError, backup.BackupError, SnapshotError, OSError, ValueError, KeyError):
            protected.append(name)
            continue
        total_files += len(tree["files"])
        total_bytes += sum(item["bytes"] for item in tree["files"].values())
        if total_files > MAX_FILES or total_bytes > MAX_BYTES:
            raise CleanupError("backup_catalog_too_large")
        bundles.append({"name": name, "directory_id": inode, "created_at": created.isoformat(),
                        "manifest_sha256": tree["files"]["manifest.json"]["sha256"], **tree})
    bundles.sort(key=lambda item: (item["created_at"], item["name"]), reverse=True)
    keep, candidates = [], []
    for index, bundle in enumerate(bundles):
        if index < selected["minimum"] or timestamp(bundle["created_at"]) >= cutoff:
            keep.append({key: bundle[key] for key in ("name", "directory_id", "manifest_sha256")})
        else:
            candidates.append(bundle)
    report = {"format": 1, "scope": retention_scope(settings), "database": identity, "root": str(root),
              "directory_id": directory_id(root), "policy": selected, "cutoff": cutoff.isoformat(),
              "keep": keep, "protected": protected, "candidates": candidates}
    report["digest"] = digest(report)
    return report


async def retire_backups(db, settings, root, expected_digest, actor, reason, **kwargs):
    report = await inspect_backups(db, settings, root, **kwargs)
    await administrator(db, actor, reason)
    request = digest({"action": "retire_backups", "digest": expected_digest, "reason": reason.strip()})
    old = await db.scalar(select(AdminAction).where(AdminAction.actor_id == actor, AdminAction.client_request_id == request))
    if old:
        return {"receipt_id": old.id, **old.result}
    if expected_digest != report["digest"]:
        raise CleanupError("inventory_changed")
    return await receipt(db, actor, reason, "retire_backups", request,
        {"status": "retired", "count": len(report["candidates"]), "manifest": report}, kwargs.get("now") or utc_now())


def retired_manifest(row):
    if row is None or row.action != "retire_backups" or row.status != "completed":
        raise CleanupError("retirement_receipt_required")
    value = row.result.get("manifest")
    if (not isinstance(value, dict) or value.get("scope") not in {SCOPE, CONTAINER_SCOPE} or value.get("format") != 1
            or value.get("digest") != digest({k: v for k, v in value.items() if k != "digest"})):
        raise CleanupError("invalid_retirement_manifest")
    return value


def validate_remaining(root, item):
    path = checked(root, item["name"])
    if not path.exists():
        return
    if directory_id(path) != item["directory_id"]:
        raise CleanupError("backup_directory_changed")
    current = inventory(path)
    if not set(current["directories"]) <= set(item["directories"]):
        raise CleanupError("backup_directory_changed")
    for key, value in current["files"].items():
        if item["files"].get(key) != value:
            raise CleanupError("backup_file_changed")


async def purge_backups(db, settings, root, batch_id, actor, reason, *, users_stopped=False, now=None):
    root, selected = await guard(db, settings, root, users_stopped)
    await administrator(db, actor, reason)
    row = await db.get(AdminAction, batch_id)
    report = retired_manifest(row)
    if (report["scope"] != retention_scope(settings) or report["database"] != await database_identity(db) or report["root"] != str(root)
            or report["directory_id"] != directory_id(root) or report["policy"] != selected):
        raise CleanupError("retirement_scope_mismatch")
    if await db.scalar(text("SELECT xmin::text::bigint = txid_current() % 4294967296 FROM v2_admin_actions WHERE id=:id"),
                       {"id": batch_id}):
        raise CleanupError("retirement_commit_required")
    request = digest({"action": "purge_backups", "batch_id": batch_id, "reason": reason.strip()})
    old = await db.scalar(select(AdminAction).where(AdminAction.actor_id == actor, AdminAction.client_request_id == request))
    if old:
        return {"receipt_id": old.id, **old.result}
    if not report["candidates"]:
        return await receipt(db, actor, reason, "purge_backups", request,
            {"status": "purged", "batch_id": batch_id, "count": 0}, now or utc_now())
    if len(report["keep"]) < selected["minimum"]:
        raise CleanupError("insufficient_retained_backups")
    # A surviving path is insufficient: all retained bundles must still verify.
    for item in report["keep"]:
        path = checked(root, item["name"])
        if directory_id(path) != item["directory_id"]:
            raise CleanupError("retained_backup_changed")
        manifest = backup.verify_bundle(path, expected_manifest_sha256=item["manifest_sha256"])
        if manifest["scope"] != bundle_scope(settings) or manifest["database_identity"] != report["database"]:
            raise CleanupError("retained_backup_changed")
        inventory(path)  # Also rejects hardlinks and special files.
    cutoff = timestamp(report["cutoff"])
    current_cutoff = (now or utc_now()).astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0) - timedelta(days=selected["days"])
    if current_cutoff < cutoff:
        raise CleanupError("retention_clock_moved_backwards")
    for item in report["candidates"]:
        if timestamp(item["created_at"]) >= cutoff or "/" in item["name"] or "\\" in item["name"]:
            raise CleanupError("invalid_retirement_manifest")
        validate_remaining(root, item)
    # Preflight the entire batch before the first unlink. Missing files are allowed
    # only because the retirement was committed by a previous transaction.
    for item in report["candidates"]:
        path = checked(root, item["name"])
        if not path.exists():
            continue
        for key in sorted(item["files"], key=lambda key: (key == "manifest.json", key)):
            target = checked(path, key)
            if target.exists():
                if directory_id(path) != item["directory_id"] or regular(target) != item["files"][key]:
                    raise CleanupError("backup_file_changed")
                target.unlink()
        for key in sorted(item["directories"], key=lambda key: (-key.count("/"), key)):
            target = checked(path, key)
            if target.exists():
                target.rmdir()  # Refuses unexpected contents; never recursive.
        path.rmdir()
    return await receipt(db, actor, reason, "purge_backups", request,
        {"status": "purged", "batch_id": batch_id, "count": len(report["candidates"]),
         "manifest_digest": report["digest"]}, now or utc_now())


async def run(settings, args):
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            options = {"users_stopped": args.backup_users_stopped}
            if args.command == "inspect":
                return await inspect_backups(db, settings, args.root, **options)
            if args.command == "retire":
                return await retire_backups(db, settings, args.root, args.expected_digest, args.actor_id, args.reason, **options)
            return await purge_backups(db, settings, args.root, args.batch_id, args.actor_id, args.reason, **options)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "retire", "purge"])
    parser.add_argument("root", type=Path)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--backup-users-stopped", action="store_true")
    parser.add_argument("--expected-digest")
    parser.add_argument("--actor-id")
    parser.add_argument("--reason")
    parser.add_argument("--batch-id")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise CleanupError("configuration_file_missing")
        result = asyncio.run(run(Settings(_env_file=args.env_file), args))
    except Exception as exc:
        code = str(exc) if isinstance(exc, (CleanupError, backup.BackupError, SnapshotError)) else "backup_retention_unavailable"
        print(json.dumps({"status": "refused", "code": code}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
