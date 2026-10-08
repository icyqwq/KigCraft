"""Explicit, reversible local-trial isolation of unregistered installed objects.

Never deletes Asset rows or expires uploads, drafts, trash, runtime state or backups.
All API/worker/import processes must use storage coordination; stop older binaries.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from uuid import uuid4

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assets.coordination import STORAGE_LOCK
from app.core.config import Settings
from app.core.time import utc_now
from app.db.v2_schema import require_v2_schema
from app.design.models import Asset, Operation
from app.operations.service import TERMINAL

FORMAT = 1
SCOPE = "v2_unregistered_installed_objects"
DIRECTORIES = ("uploads", "masters", "legacy")
OBJECT = re.compile(r"^(uploads|masters|legacy)/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(png|jpg|webp)$")
MAX_FILES = 10_000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_BATCH_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


class CleanupError(ValueError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def linked(path):
    return path.is_symlink() or path.is_junction()


def root_path(root):
    root = Path(root)
    if not root.is_absolute() or not root.is_dir():
        raise CleanupError("existing_absolute_storage_root_required")
    if any(linked(part) for part in (root, *root.parents)):
        raise CleanupError("unsafe_storage_path")
    return root.resolve()


def checked(root, key):
    # Accept only a relative, forward-slash path composed of ordinary segments.
    if not isinstance(key, str) or not key or "\\" in key or ":" in key:
        raise CleanupError("unsafe_storage_path")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise CleanupError("unsafe_storage_path")
    current = root
    for part in parts:
        current /= part
        if linked(current):
            raise CleanupError("unsafe_storage_path")
    if not current.resolve().is_relative_to(root):
        raise CleanupError("unsafe_storage_path")
    return current


def fingerprint(path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or linked(path) or info.st_size > MAX_FILE_BYTES:
        raise CleanupError("unsafe_or_oversized_object")
    total = 0
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise CleanupError("unsafe_or_oversized_object")
            value.update(chunk)
    after = path.stat(follow_symlinks=False)
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ) or total != info.st_size:
        raise CleanupError("object_changed")
    return {"bytes": total, "sha256": value.hexdigest()}


def write_new(path, value):
    data = encoded(value)
    if len(data) > MAX_MANIFEST_BYTES:
        raise CleanupError("manifest_too_large")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


async def maintenance_lock(db):
    if db.new or db.dirty or db.deleted:
        raise CleanupError("clean_maintenance_transaction_required")
    if await db.scalar(text("SHOW transaction_isolation")) != "read committed":
        raise CleanupError("read_committed_required")
    if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": STORAGE_LOCK}):
        raise CleanupError("storage_writer_busy")


async def database_identity(db):
    # No connection strings, usernames, passwords or account metadata in the journal.
    row = (await db.execute(text(
        "SELECT current_database(), inet_server_addr()::text, inet_server_port()"
    ))).one()
    return digest(list(row))


async def inventory_locked(db, root):
    rows = (await db.execute(select(Asset.id, Asset.object_key).order_by(Asset.id).limit(MAX_FILES + 1))).all()
    if len(rows) > MAX_FILES:
        raise CleanupError("inventory_too_large")
    # Compare native canonical paths too: Windows path matching is case-insensitive.
    # Ambiguous traversal/link keys refuse maintenance rather than losing a reference.
    registered = {os.path.normcase(str(checked(root, row.object_key))) for row in rows}
    # Including null/unknown future states is deliberate: absence of a known terminal
    # result is never proof that a failed publication can no longer be recovered.
    pending = await db.scalar(select(Operation.id).where(or_(
        Operation.state.not_in(TERMINAL),
        and_(Operation.dispatch_intent_at.is_not(None), or_(
            Operation.upstream_status.is_(None),
            Operation.upstream_status.not_in(["completed", "failed", "interrupted"]))),
        and_(Operation.reserved_units > 0, Operation.reservation_closed.is_(False)),
    )).limit(1))
    candidates, skipped = [], []
    count = total = 0
    for directory in DIRECTORIES:
        folder = checked(root, directory)
        if not folder.exists():
            continue
        if not folder.is_dir():
            raise CleanupError("unsafe_storage_path")
        for path in folder.iterdir():
            count += 1
            if count > MAX_FILES:
                raise CleanupError("inventory_too_large")
            key = directory + "/" + path.name
            if os.path.normcase(str(path)) in registered:
                continue
            if linked(path):
                raise CleanupError("unsafe_storage_path")
            if not OBJECT.fullmatch(key) or not path.is_file() or path.stat().st_nlink != 1:
                skipped.append(key)
                continue
            value = fingerprint(path)
            total += value["bytes"]
            if total > MAX_BATCH_BYTES:
                raise CleanupError("inventory_too_large")
            candidates.append({"key": key, **value})
    candidates.sort(key=lambda item: item["key"])
    skipped.sort()
    report = {
        "format": FORMAT, "scope": SCOPE, "database": await database_identity(db),
        "storage_root": str(root), "registered_assets": len(rows),
        "registry_digest": digest([list(row) for row in rows]),
        "blocked": "operation_unconfirmed" if pending is not None else None,
        "candidates": candidates, "candidate_bytes": total, "skipped": skipped,
    }
    report["digest"] = digest(report)
    return report


async def inspect_orphans(db, storage_root):
    root = root_path(storage_root)
    await maintenance_lock(db)
    return await inventory_locked(db, root)


def assert_bytes(path, item):
    if fingerprint(path) != {"bytes": item["bytes"], "sha256": item["sha256"]}:
        raise CleanupError("object_changed")


async def quarantine_orphans(db, storage_root, expected_digest):
    root = root_path(storage_root)
    await maintenance_lock(db)
    report = await inventory_locked(db, root)
    if not expected_digest or report["digest"] != expected_digest:
        raise CleanupError("inventory_changed")
    if report["blocked"]:
        raise CleanupError(report["blocked"])
    if not report["candidates"]:
        return {"status": "nothing_to_quarantine", "count": 0}
    batch_id = str(uuid4())
    base = checked(root, ".quarantine")
    base.mkdir(exist_ok=True)
    batch = checked(root, ".quarantine/" + batch_id)
    batch.mkdir(exist_ok=False)
    # Write-ahead journal is complete before the first source pathname is removed.
    # After interruption, restore accepts source-only, quarantine-only, and both.
    write_new(batch / "manifest.json", report)
    write_new(batch / "prepared.json", {
        "batch_id": batch_id, "manifest_digest": report["digest"],
        "prepared_at": utc_now().isoformat(), "reason": "unregistered_installed_objects",
        "permanent_deletion": False,
    })
    for item in report["candidates"]:
        source = checked(root, item["key"])
        target = checked(root, ".quarantine/" + batch_id + "/objects/" + item["key"])
        target.parent.mkdir(parents=True, exist_ok=True)
        assert_bytes(source, item)
        os.link(source, target)  # same volume, exclusive; never overwrites
        assert_bytes(target, item)
        source.unlink()
    write_new(batch / "completed.json", {"manifest_digest": report["digest"], "count": len(report["candidates"])})
    return {"status": "quarantined", "batch_id": batch_id, "count": len(report["candidates"]),
            "bytes": report["candidate_bytes"], "manifest_digest": report["digest"]}


async def restore_quarantine(db, storage_root, batch_id):
    root = root_path(storage_root)
    if not isinstance(batch_id, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", batch_id
    ):
        raise CleanupError("invalid_batch")
    await maintenance_lock(db)
    batch_key = ".quarantine/" + batch_id
    manifest = checked(root, batch_key + "/manifest.json")
    if manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise CleanupError("manifest_too_large")
    try:
        report = json.loads(manifest.read_text(encoding="utf-8"))
        signature = report.pop("digest")
        if signature != digest(report):
            raise CleanupError("manifest_changed")
        if (report["format"] != FORMAT or report["scope"] != SCOPE or
            report["database"] != await database_identity(db) or report["storage_root"] != str(root)):
            raise CleanupError("manifest_scope_mismatch")
        items = report["candidates"]
        if not isinstance(items, list) or len(items) > MAX_FILES:
            raise CleanupError("invalid_manifest")
        keys = [item["key"] for item in items]
        if len(set(keys)) != len(keys) or any(not OBJECT.fullmatch(key) for key in keys):
            raise CleanupError("invalid_manifest")
        if any(set(item) != {"key", "bytes", "sha256"} or type(item["bytes"]) is not int or
               not 0 <= item["bytes"] <= MAX_FILE_BYTES or
               not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in items):
            raise CleanupError("invalid_manifest")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CleanupError):
            raise
        raise CleanupError("invalid_manifest") from None
    registered = (await db.execute(select(Asset.object_key, Asset.sha256, Asset.byte_size)
        .limit(MAX_FILES + 1))).all()
    if len(registered) > MAX_FILES:
        raise CleanupError("inventory_too_large")
    current_assets = {os.path.normcase(str(checked(root, row.object_key))): row for row in registered}
    for item in items:
        current = current_assets.get(os.path.normcase(str(checked(root, item["key"]))))
        if current is not None and (current.sha256 != item["sha256"] or current.byte_size != item["bytes"]):
            raise CleanupError("registered_asset_conflict")
    # Prevalidate every destination before any restoration. Never overwrite files
    # created by a later successful upload/publication/import.
    for item in items:
        source = checked(root, batch_key + "/objects/" + item["key"])
        target = checked(root, item["key"])
        if not source.exists() and not target.exists():
            raise CleanupError("quarantined_object_missing")
        for path in (source, target):
            if path.exists():
                assert_bytes(path, item)
    for item in items:
        source = checked(root, batch_key + "/objects/" + item["key"])
        target = checked(root, item["key"])
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
        if source.exists():
            assert_bytes(target, item)
            source.unlink()
    receipt = checked(root, batch_key + "/restored.json")
    result = {"status": "restored", "batch_id": batch_id, "count": len(items), "manifest_digest": signature}
    if not receipt.exists():
        write_new(receipt, result)
    return result


async def run(settings, action, expected_digest=None, batch_id=None):
    from app.assets.repository import require_filesystem
    require_filesystem(settings)
    if not settings.v2_enabled or settings.app_env not in {"development", "test"} or settings.v2_runtime_mode != "local":
        raise CleanupError("local_trial_only")
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            if action == "inspect":
                return await inspect_orphans(db, Path(settings.v2_storage_dir))
            if action == "quarantine":
                return await quarantine_orphans(db, Path(settings.v2_storage_dir), expected_digest)
            return await restore_quarantine(db, Path(settings.v2_storage_dir), batch_id)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inspect", "quarantine", "restore"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--expected-digest")
    parser.add_argument("--batch-id")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise CleanupError("configuration_file_missing")
        result = asyncio.run(run(Settings(_env_file=args.env_file), args.action, args.expected_digest, args.batch_id))
    except Exception as exc:
        code = str(exc) if isinstance(exc, CleanupError) else "maintenance_unavailable"
        print(json.dumps({"status": "refused", "code": code}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
