"""Explicit retirement and resumable purge of completed-operation temporary files.

Retirement commits an audit manifest before bytes may be removed. Inputs, native
history, original generation outputs and formal assets are never purge targets.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import errno
import os
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assets.repository import storage_io
from app.auth.models import UserRole
from app.codex_runtime.container_runtime import ContainerRuntime
from app.codex_runtime.selection import container_spec, runtime_allowed
from app.codex_runtime.profile_lock import ProfileLease, ProfileLockError
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import User
from app.db.v2_schema import require_v2_schema
from app.design.models import AdminAction, Asset, Operation, Revision, RuntimeControl
from app.maintenance.orphan_assets import (
    CleanupError, MAX_BATCH_BYTES, MAX_FILES, checked, database_identity, digest,
    fingerprint, linked, root_path,
)
from app.maintenance.upload_retention import lock_registry
from app.maintenance.remote_assets import RemoteAssets, deployment_allowed
from app.maintenance.backup_assets import cache_root
from app.operations.service import TERMINAL

SCOPE = "v2_operation_temporary_files"
MAX_OPERATIONS = 100
MAX_RECEIPTS = 10000
STAGING_NAME = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg)$")


def closed(operation):
    return (
        operation.state in TERMINAL
        and operation.reservation_closed
        and operation.lease_owner is None
        and operation.lease_expires_at is None
        and (operation.dispatch_intent_at is None
             or operation.upstream_status in {"completed", "failed", "interrupted"})
        and (operation.context_allocation or {}).get("state") not in {"sent", "unknown"}
    )


async def guard(db, settings, *, writers_stopped):
    if not writers_stopped:
        raise CleanupError("writers_stopped_acknowledgement_required")
    if not deployment_allowed(settings):
        raise CleanupError("maintenance_configuration_required")
    if settings.v2_runtime_mode == "container" and (not settings.v2_worker_user_id
            or not runtime_allowed(settings, settings.v2_worker_user_id)):
        raise CleanupError("runtime_account_required")
    if not settings.v2_runtime_retention_days:
        raise CleanupError("runtime_retention_disabled")
    await lock_registry(db)
    control = await db.get(RuntimeControl, "global")
    if control is None or not control.blocked:
        raise CleanupError("generation_must_be_paused")
    pending = await db.scalar(select(Operation.id).where(or_(
        Operation.state.not_in(TERMINAL), Operation.reservation_closed.is_(False),
        Operation.lease_owner.is_not(None), Operation.lease_expires_at.is_not(None),
        and_(Operation.dispatch_intent_at.is_not(None), or_(
            Operation.upstream_status.is_(None),
            Operation.upstream_status.not_in(["completed", "failed", "interrupted"]))),
    )).limit(1))
    if pending:
        raise CleanupError("operation_unconfirmed")
    if await db.scalar(select(AdminAction.id).where(
            AdminAction.status.in_(["pending", "running"])).limit(1)):
        raise CleanupError("admin_action_pending")
    if settings.v2_runtime_mode == "container":
        spec = container_spec(settings, settings.v2_worker_user_id)
        boundary = root_path(Path(spec.base))
        root = root_path(Path(spec.work))
    else:
        root = boundary = root_path(Path(settings.v2_runtime_dir))
    assets = (cache_root(settings.v2_storage_dir) if settings.v2_asset_backend == "minio"
              else root_path(Path(settings.v2_storage_dir)))
    if boundary.is_relative_to(assets) or assets.is_relative_to(boundary):
        raise CleanupError("runtime_asset_root_overlap")
    return root, RemoteAssets(settings) if settings.v2_asset_backend == "minio" else assets


@asynccontextmanager
async def environment(db, settings, *, writers_stopped):
    root, storage = await guard(db, settings, writers_stopped=writers_stopped)
    if settings.v2_runtime_mode == "local":
        yield root, storage, None
        return
    runtime = ContainerRuntime(settings)
    if root_path(runtime.work_root) != root:
        raise CleanupError("runtime_account_root_mismatch")
    # The production scope holds the account's host profile lock and inspects
    # the stopped container. No start/stop/exec command or model RPC is issued.
    async with runtime.operation():
        await runtime.verify_stopped()
        yield root, storage, runtime
        await runtime.verify_stopped()


def scope(settings, storage):
    return {"runtime_mode": settings.v2_runtime_mode,
            "owner_id": settings.v2_worker_user_id if settings.v2_runtime_mode == "container" else None,
            "asset_target": str(storage)}


def identity(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def regular(root, key):
    path = checked(root, key)
    if path.stat(follow_symlinks=False).st_nlink != 1:
        raise CleanupError("linked_runtime_file")
    return fingerprint(path)


def temporary_key(key):
    if not isinstance(key, str):
        return False
    parts = key.split("/")
    return (len(parts) >= 3 and identity(parts[0])
            and (parts[1] == "review" or
                 (len(parts) == 3 and parts[1] == "staging" and STAGING_NAME.fullmatch(parts[2]))))


async def published(db, operation, storage):
    """No retirement of an unpublished deliverable unless cancellation closed it."""
    if operation.cancel_requested:
        return
    for slot, descriptor in (operation.staged_outputs or {}).items():
        revision = await db.scalar(select(Revision).where(
            Revision.operation_id == operation.id, Revision.delivery_key == slot))
        asset = await db.get(Asset, revision.clean_asset_id) if revision else None
        if (asset is None or asset.status != "ready" or asset.sha256 != descriptor.get("sha256")
                or asset.owner_id != operation.owner_id):
            raise CleanupError("published_copy_unavailable")
        if isinstance(storage, RemoteAssets):
            # Always validate the current remote object, even with a warm cache.
            await storage_io(storage.store.read, asset.object_key, asset.sha256, asset.byte_size)
        elif regular(storage, asset.object_key) != {"sha256":asset.sha256, "bytes":asset.byte_size}:
            raise CleanupError("published_copy_unavailable")


async def inspect_runtime(db, settings, *, writers_stopped=False, now=None, after=None, limit=MAX_OPERATIONS):
    async with environment(db, settings, writers_stopped=writers_stopped) as (root, storage, _):
        return await inspect_files(db, settings, root, storage, now=now, after=after, limit=limit)


async def inspect_files(db, settings, root, storage, *, now=None, after=None, limit=MAX_OPERATIONS):
    if (after is not None and not identity(after)) or type(limit) is not int or not 1 <= limit <= MAX_OPERATIONS:
        raise CleanupError("invalid_retention_page")
    now = now or utc_now()
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=settings.v2_runtime_retention_days)
    query = select(Operation).where(Operation.updated_at < cutoff)
    if settings.v2_runtime_mode == "container":
        query = query.where(Operation.owner_id == settings.v2_worker_user_id)
    if after:
        query = query.where(Operation.id > after)
    rows = list(await db.scalars(query.order_by(Operation.id).limit(limit + 1)))
    more, rows = len(rows) > limit, rows[:limit]
    candidates, retained, operations = [], [], {}
    total = count = 0
    for operation in rows:
        if not identity(operation.id) or not closed(operation):
            retained.append({"operation_id":operation.id, "reason":"operation_unconfirmed"})
            continue
        await published(db, operation, storage)
        operations[operation.id] = operation.updated_at.isoformat()
        files = []
        for descriptor in (operation.staged_outputs or {}).values():
            name = descriptor.get("filename")
            if not isinstance(name, str) or not STAGING_NAME.fullmatch(name) or name not in {descriptor.get("delivery_key", "") + ".png", descriptor.get("delivery_key", "") + ".jpg"}:
                raise CleanupError("invalid_staging_descriptor")
            key = operation.id + "/staging/" + name
            path = checked(root, key)
            if path.exists():
                files.append((key, descriptor.get("sha256")))
            elif not await retired_staging(db, operation.id, descriptor):
                raise CleanupError("staging_dependency_missing")
        review_key = operation.id + "/review"
        review = checked(root, review_key)
        if review.exists():
            if not review.is_dir():
                raise CleanupError("unsafe_review_directory")
            stack = [review]
            while stack:
                directory = stack.pop()
                for child in sorted(directory.iterdir()):
                    count += 1
                    if count > MAX_FILES:
                        raise CleanupError("runtime_inventory_too_large")
                    key = child.relative_to(root).as_posix()
                    child = checked(root, key)
                    if linked(child):
                        raise CleanupError("unsafe_storage_path")
                    if child.is_dir():
                        stack.append(child)
                    else:
                        files.append((key, None))
        for key, expected_hash in sorted(set(files)):
            count += 1
            if count > MAX_FILES:
                raise CleanupError("runtime_inventory_too_large")
            path = checked(root, key)
            if path.stat().st_mtime >= cutoff.timestamp():
                retained.append({"key":key, "reason":"file_recent"})
                continue
            value = regular(root, key)
            if expected_hash is not None and value["sha256"] != expected_hash:
                raise CleanupError("staging_hash_mismatch")
            total += value["bytes"]
            if total > MAX_BATCH_BYTES:
                raise CleanupError("runtime_inventory_too_large")
            candidates.append({"key":key, "mtime_ns":path.stat().st_mtime_ns, **value})
    report = {"format":2, **scope(settings, storage), "scope":SCOPE, "database":await database_identity(db),
              "runtime_root":str(root), "policy_days":settings.v2_runtime_retention_days,
              "cutoff":cutoff.isoformat(), "after":after, "limit":limit,
              "next_after":rows[-1].id if more else None,
              "operations":operations, "candidates":candidates, "retained":retained, "candidate_bytes":total}
    report["digest"] = digest(report)
    return report


async def administrator(db, actor_id, reason):
    actor = await db.get(User, actor_id)
    if actor is None or actor.is_banned or actor.role != UserRole.admin:
        raise CleanupError("admin_required")
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
        raise CleanupError("reason_required")


async def receipt(db, actor_id, reason, action, request, result, now):
    row = AdminAction(id=str(uuid4()), actor_id=actor_id, client_request_id=request,
        request_digest=request, action=action, target_id="global", reason=reason.strip(),
        status="completed", result=result, created_at=now, started_at=now, completed_at=now)
    db.add(row)
    await db.flush()
    return {"receipt_id":row.id, **result}


async def retire_runtime(db, settings, expected_digest, actor_id, reason, **kwargs):
    report = await inspect_runtime(db, settings, **kwargs)
    await administrator(db, actor_id, reason)
    request = digest({"action":"retire_runtime", "digest":expected_digest, "reason":reason.strip()})
    existing = await db.scalar(select(AdminAction).where(
        AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
    if existing:
        return {"receipt_id":existing.id, **existing.result}
    if not expected_digest or expected_digest != report["digest"]:
        raise CleanupError("inventory_changed")
    return await receipt(db, actor_id, reason, "retire_runtime", request,
        {"status":"retired", "count":len(report["candidates"]), "manifest":report}, kwargs.get("now") or utc_now())


def manifest(row):
    if row is None or row.action != "retire_runtime" or row.status != "completed":
        raise CleanupError("retirement_receipt_required")
    value = row.result.get("manifest")
    if (not isinstance(value, dict) or value.get("scope") != SCOPE or value.get("format") not in {1, 2}
            or value.get("digest") != digest({k:v for k,v in value.items() if k != "digest"})):
        raise CleanupError("invalid_retirement_manifest")
    if value["format"] == 2 and (
            value.get("runtime_mode") not in {"local", "container"}
            or (value["runtime_mode"] == "container" and not identity(value.get("owner_id")))
            or (value["runtime_mode"] == "local" and value.get("owner_id") is not None)
            or not isinstance(value.get("asset_target"), str) or not value["asset_target"]):
        raise CleanupError("invalid_retirement_manifest")
    items = value.get("candidates")
    if not isinstance(items, list) or len(items) > MAX_FILES:
        raise CleanupError("invalid_retirement_manifest")
    seen = set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {"key", "sha256", "bytes", "mtime_ns"}
                or not temporary_key(item.get("key")) or item["key"] in seen
                or not isinstance(item.get("sha256"), str) or not re.fullmatch("[0-9a-f]{64}", item["sha256"])
                or type(item.get("bytes")) is not int or item["bytes"] < 0
                or type(item.get("mtime_ns")) is not int):
            raise CleanupError("invalid_retirement_manifest")
        seen.add(item["key"])
    return value


async def retired_staging(db, operation_id, descriptor):
    """A committed retirement is a tombstone, including a partly completed purge."""
    key = operation_id + "/staging/" + str(descriptor.get("filename", ""))
    if not temporary_key(key):
        return False
    rows = list(await db.scalars(select(AdminAction).where(
        AdminAction.action == "retire_runtime", AdminAction.status == "completed")
        .order_by(AdminAction.created_at.desc()).limit(MAX_RECEIPTS + 1)))
    if len(rows) > MAX_RECEIPTS:
        raise CleanupError("retention_history_too_large")
    for row in rows:
        value = manifest(row)
        # Root/database relocation during restore is legitimate: operation IDs and
        # immutable descriptor hashes survive the independently verified bundle.
        for item in value["candidates"]:
            if item["key"] == key and item["sha256"] == descriptor.get("sha256"):
                return row.id
    return False


async def purge_runtime(db, settings, batch_id, actor_id, reason, *, writers_stopped=False, now=None):
    async with environment(db, settings, writers_stopped=writers_stopped) as (root, storage, runtime):
        return await purge_files(db, settings, root, storage, runtime, batch_id, actor_id, reason, now=now)


async def purge_files(db, settings, root, storage, runtime, batch_id, actor_id, reason, *, now=None):
    await administrator(db, actor_id, reason)
    row = await db.get(AdminAction, batch_id)
    value = manifest(row)
    if value["runtime_root"] != str(root) or value["database"] != await database_identity(db):
        raise CleanupError("retirement_scope_mismatch")
    if value["format"] == 1:
        if settings.v2_runtime_mode != "local" or settings.v2_asset_backend != "filesystem":
            raise CleanupError("retirement_scope_mismatch")
    elif any(value.get(key) != item for key, item in scope(settings, storage).items()):
        raise CleanupError("retirement_scope_mismatch")
    # Ensure the retirement is visible to another transaction before any unlink.
    # A caller cannot create a retirement and purge it in the same transaction.
    if await db.scalar(text("SELECT xmin::text::bigint = txid_current() % 4294967296 FROM v2_admin_actions WHERE id=:id"),
                       {"id":batch_id}):
        raise CleanupError("retirement_commit_required")
    request = digest({"action":"purge_runtime", "batch_id":batch_id, "reason":reason.strip()})
    existing = await db.scalar(select(AdminAction).where(
        AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
    if existing:
        return {"receipt_id":existing.id, **existing.result}
    for opid in sorted({item["key"].split("/")[0] for item in value["candidates"]}):
        operation = await db.get(Operation, opid)
        if (operation is None or not closed(operation)
                or (settings.v2_runtime_mode == "container" and operation.owner_id != settings.v2_worker_user_id)):
            raise CleanupError("operation_unconfirmed")
        if value.get("operations", {}).get(opid) != operation.updated_at.isoformat():
            raise CleanupError("operation_changed")
        for item in value["candidates"]:
            if item["key"].startswith(opid + "/staging/") and not any(
                    d.get("filename") == item["key"].split("/")[-1] and d.get("sha256") == item["sha256"]
                    for d in (operation.staged_outputs or {}).values()):
                raise CleanupError("staging_descriptor_changed")
        await published(db, operation, storage)
    if runtime is not None:
        await runtime.verify_stopped()
    # Validate the complete batch before deleting any file. On interruption or DB
    # rollback, the committed retirement permits a retry with missing files.
    for item in value["candidates"]:
        path = checked(root, item["key"])
        if path.exists() and (path.stat().st_mtime_ns != item["mtime_ns"] or regular(root, item["key"]) != {"sha256":item["sha256"], "bytes":item["bytes"]}):
            raise CleanupError("runtime_file_changed")
    for item in value["candidates"]:
        path = checked(root, item["key"])
        if path.exists():
            if path.stat().st_mtime_ns != item["mtime_ns"] or regular(root, item["key"]) != {"sha256":item["sha256"], "bytes":item["bytes"]}:
                raise CleanupError("runtime_file_changed")
            path.unlink()
    # Remove only empty directories that contained the reviewed files; never
    # recursively remove a workspace, native output, or an unlisted file.
    directories = set()
    for item in value["candidates"]:
        parent = Path(item["key"]).parent
        while len(parent.parts) >= 2:
            directories.add(parent.as_posix())
            parent = parent.parent
    for key in sorted(directories, key=lambda item: (-item.count("/"), item)):
        path = checked(root, key)
        if path.exists():
            try:
                path.rmdir()
            except OSError as exc:
                if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                    raise
    return await receipt(db, actor_id, reason, "purge_runtime", request,
        {"status":"purged", "batch_id":batch_id, "count":len(value["candidates"]),
         "manifest_digest":value["digest"]}, now or utc_now())


async def run(settings, args):
    if not args.writers_stopped:
        raise CleanupError("writers_stopped_acknowledgement_required")
    lease = None
    if settings.v2_runtime_mode == "local":
        lease = ProfileLease(Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))).expanduser().resolve())
        lease.acquire()
    engine = None
    try:
        engine = create_async_engine(settings.database_url, hide_parameters=True)
        await require_v2_schema(settings=settings, engine=engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as db:
            if args.command == "inspect":
                return await inspect_runtime(db, settings, writers_stopped=True, after=args.after, limit=args.limit)
            if args.command == "retire":
                return await retire_runtime(db, settings, args.expected_digest, args.actor_id, args.reason,
                    writers_stopped=True, after=args.after, limit=args.limit)
            return await purge_runtime(db, settings, args.batch_id, args.actor_id, args.reason, writers_stopped=True)
    finally:
        try:
            if engine is not None:
                await engine.dispose()
        finally:
            if lease is not None:
                lease.release(clean=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "retire", "purge"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--after")
    parser.add_argument("--limit", type=int, default=MAX_OPERATIONS)
    parser.add_argument("--expected-digest")
    parser.add_argument("--batch-id")
    parser.add_argument("--actor-id")
    parser.add_argument("--reason")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise CleanupError("configuration_file_missing")
        result = asyncio.run(run(Settings(_env_file=args.env_file), args))
    except Exception as exc:
        code = exc.code if isinstance(exc, ProfileLockError) else str(exc) if isinstance(exc, CleanupError) else "maintenance_unavailable"
        print(json.dumps({"status":"refused", "code":code}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
