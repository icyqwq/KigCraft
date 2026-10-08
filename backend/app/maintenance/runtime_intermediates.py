"""Retire unregistered staging intermediates after business/native reference checks."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import re

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assets.repository import storage_io
from app.codex_runtime.profile_lock import ProfileLease
from app.core.config import Settings
from app.core.time import utc_now
from app.db.v2_schema import require_v2_schema
from app.design.models import AdminAction, Operation
from app.maintenance import runtime_retention as runtime
from app.maintenance.orphan_assets import CleanupError, MAX_BATCH_BYTES, MAX_FILES, checked, database_identity, digest, root_path
from app.maintenance.runtime_references import MAX_CANDIDATES, business_references, native_references

SCOPE = "v2_unregistered_runtime_intermediates"
RETIRE = "retire_runtime_orphans"
PURGE = "purge_runtime_orphans"
NAME = re.compile(r"(?:[0-9a-f]{64}\.(?:png|jpg)|\.(?:staging|local)-[a-z0-9_]{8})")


def valid_key(key):
    if not isinstance(key, str): return False
    parts = key.split("/")
    return len(parts) == 3 and runtime.identity(parts[0]) and parts[1] == "staging" and bool(NAME.fullmatch(parts[2]))


@asynccontextmanager
async def environment(db, settings, writers_stopped, profile):
    async with runtime.environment(db, settings, writers_stopped=writers_stopped) as (root, storage, container):
        if container is not None:
            native = root_path(container.profile)
            if profile is not None and root_path(profile) != native:
                raise CleanupError("native_profile_scope_mismatch")
            yield root, storage, container, native
        else:
            native = root_path(profile or Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))))
            if native.is_relative_to(root) or root.is_relative_to(native):
                raise CleanupError("runtime_native_root_overlap")
            lease = ProfileLease(native).acquire()
            try: yield root, storage, None, native
            finally: lease.release(clean=True)


async def references(db, settings, profile, candidates):
    native, proof = await storage_io(native_references, profile, candidates, settings.v2_codex_version)
    return native | await business_references(db, candidates), proof


async def inspect_locked(db, settings, root, storage, profile, *, now=None, after=None, limit=100):
    if (after is not None and not runtime.identity(after)) or type(limit) is not int or not 1 <= limit <= 100:
        raise CleanupError("invalid_retention_page")
    now = now or utc_now()
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=settings.v2_runtime_retention_days)
    query = select(Operation).where(Operation.updated_at < cutoff)
    if settings.v2_runtime_mode == "container":
        query = query.where(Operation.owner_id == settings.v2_worker_user_id)
    if after: query = query.where(Operation.id > after)
    rows = list(await db.scalars(query.order_by(Operation.id).limit(limit + 1)))
    more, rows = len(rows) > limit, rows[:limit]
    candidates, retained, operations = [], [], {}
    count = total = 0
    for operation in rows:
        if not runtime.identity(operation.id) or not runtime.closed(operation):
            retained.append({"operation_id": operation.id, "reason": "operation_unconfirmed"})
            continue
        await runtime.published(db, operation, storage)
        operations[operation.id] = operation.updated_at.isoformat()
        folder = checked(root, operation.id + "/staging")
        if not folder.exists(): continue
        if not folder.is_dir(): raise CleanupError("unsafe_staging_directory")
        registered = {value.get("filename") for value in (operation.staged_outputs or {}).values()}
        for path in folder.iterdir():
            count += 1
            if count > MAX_FILES: raise CleanupError("runtime_inventory_too_large")
            key = path.relative_to(root).as_posix()
            path = checked(root, key)
            if path.name in registered: continue
            if not valid_key(key):
                retained.append({"key": key, "reason": "unrecognized_file"})
                continue
            if path.stat().st_mtime >= cutoff.timestamp():
                retained.append({"key": key, "reason": "file_recent"})
                continue
            info = runtime.regular(root, key)
            total += info["bytes"]
            if total > MAX_BATCH_BYTES or len(candidates) >= MAX_CANDIDATES:
                raise CleanupError("intermediate_batch_too_large")
            candidates.append({"key": key, "mtime_ns": path.stat().st_mtime_ns, **info})
    protected, proof = await references(db, settings, profile, candidates)
    retained.extend({"key": key, "reason": "referenced_file"} for key in sorted(protected))
    candidates = sorted((item for item in candidates if item["key"] not in protected), key=lambda item: item["key"])
    report = {"format": 1, "scope": SCOPE, **runtime.scope(settings, storage),
        "database": await database_identity(db), "runtime_root": str(root), "native_profile": str(profile),
        "codex_version": settings.v2_codex_version, "native_proof": proof,
        "policy_days": settings.v2_runtime_retention_days, "grace_days": settings.v2_runtime_intermediate_grace_days,
        "cutoff": cutoff.isoformat(), "after": after, "limit": limit,
        "next_after": rows[-1].id if more else None, "operations": operations,
        "candidates": candidates, "retained": sorted(retained, key=lambda item: item.get("key", item.get("operation_id", ""))),
        "candidate_bytes": sum(item["bytes"] for item in candidates)}
    report["digest"] = digest(report)
    return report


async def inspect_intermediates(db, settings, *, writers_stopped=False, profile=None, **kwargs):
    async with environment(db, settings, writers_stopped, profile) as (root, storage, _, native):
        return await inspect_locked(db, settings, root, storage, native, **kwargs)


async def retire_intermediates(db, settings, expected_digest, actor_id, reason, *,
                              writers_stopped=False, profile=None, **kwargs):
    async with environment(db, settings, writers_stopped, profile) as (root, storage, _, native):
        report = await inspect_locked(db, settings, root, storage, native, **kwargs)
        await runtime.administrator(db, actor_id, reason)
        request = digest({"action": RETIRE, "digest": expected_digest, "reason": reason.strip()})
        existing = await db.scalar(select(AdminAction).where(AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
        if existing: return {"receipt_id": existing.id, **existing.result}
        if report["digest"] != expected_digest: raise CleanupError("inventory_changed")
        return await runtime.receipt(db, actor_id, reason, RETIRE, request,
            {"status": "retired", "manifest": report, "count": len(report["candidates"])}, kwargs.get("now") or utc_now())


def manifest(row):
    if row is None or row.action != RETIRE or row.status != "completed":
        raise CleanupError("retirement_receipt_required")
    value = row.result.get("manifest")
    if (not isinstance(value, dict) or value.get("format") != 1 or value.get("scope") != SCOPE
            or value.get("digest") != digest({key: item for key, item in value.items() if key != "digest"})):
        raise CleanupError("invalid_retirement_manifest")
    items = value.get("candidates")
    if (not isinstance(items, list) or len(items) > MAX_CANDIDATES
            or type(value.get("grace_days")) is not int or not 1 <= value["grace_days"] <= 3650):
        raise CleanupError("invalid_retirement_manifest")
    seen = set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {"key", "sha256", "bytes", "mtime_ns"}
            or not valid_key(item.get("key")) or item["key"] in seen
            or not isinstance(item.get("sha256"), str) or not re.fullmatch("[0-9a-f]{64}", item["sha256"])
            or type(item.get("bytes")) is not int or item["bytes"] < 0 or type(item.get("mtime_ns")) is not int):
            raise CleanupError("invalid_retirement_manifest")
        seen.add(item["key"])
    return value


async def purge_intermediates(db, settings, batch_id, actor_id, reason, *,
                             writers_stopped=False, profile=None, now=None):
    async with environment(db, settings, writers_stopped, profile) as (root, storage, container, native):
        await runtime.administrator(db, actor_id, reason)
        row = await db.get(AdminAction, batch_id)
        value = manifest(row)
        expected = {**runtime.scope(settings, storage), "database": await database_identity(db),
                    "runtime_root": str(root), "native_profile": str(native), "codex_version": settings.v2_codex_version}
        if any(value.get(key) != item for key, item in expected.items()):
            raise CleanupError("retirement_scope_mismatch")
        if await db.scalar(text("SELECT xmin::text::bigint = txid_current() % 4294967296 FROM v2_admin_actions WHERE id=:id"), {"id": batch_id}):
            raise CleanupError("retirement_commit_required")
        now = now or utc_now()
        grace = max(value["grace_days"], settings.v2_runtime_intermediate_grace_days)
        if row.completed_at is None or now < row.completed_at + timedelta(days=grace):
            raise CleanupError("retirement_grace_period")
        request = digest({"action": PURGE, "batch_id": batch_id, "reason": reason.strip()})
        existing = await db.scalar(select(AdminAction).where(AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
        if existing: return {"receipt_id": existing.id, **existing.result}
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=settings.v2_runtime_retention_days)
        for opid in sorted({item["key"].split("/")[0] for item in value["candidates"]}):
            operation = await db.get(Operation, opid)
            if (operation is None or not runtime.closed(operation) or operation.updated_at >= cutoff
                or operation.updated_at.isoformat() != value["operations"].get(opid)
                or (settings.v2_runtime_mode == "container" and operation.owner_id != settings.v2_worker_user_id)):
                raise CleanupError("operation_changed")
            await runtime.published(db, operation, storage)
        protected, _ = await references(db, settings, native, value["candidates"])
        if protected: raise CleanupError("intermediate_reference_added")
        if container is not None: await container.verify_stopped()
        for item in value["candidates"]:
            path = checked(root, item["key"])
            if path.exists() and (path.stat().st_mtime >= cutoff.timestamp()
                or path.stat().st_mtime_ns != item["mtime_ns"]
                or runtime.regular(root, item["key"]) != {"sha256": item["sha256"], "bytes": item["bytes"]}):
                raise CleanupError("runtime_file_changed")
        for item in value["candidates"]:
            path = checked(root, item["key"])
            if path.exists():
                if (path.stat().st_mtime_ns != item["mtime_ns"]
                    or runtime.regular(root, item["key"]) != {"sha256": item["sha256"], "bytes": item["bytes"]}):
                    raise CleanupError("runtime_file_changed")
                path.unlink()
        return await runtime.receipt(db, actor_id, reason, PURGE, request,
            {"status": "purged", "batch_id": batch_id, "manifest_digest": value["digest"],
             "count": len(value["candidates"])}, now)


async def run(settings, args):
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as db:
            options = {"writers_stopped": args.writers_stopped}
            if args.command == "inspect":
                return await inspect_intermediates(db, settings, after=args.after, limit=args.limit, **options)
            if args.command == "retire":
                return await retire_intermediates(db, settings, args.expected_digest, args.actor_id, args.reason,
                    after=args.after, limit=args.limit, **options)
            return await purge_intermediates(db, settings, args.batch_id, args.actor_id, args.reason, **options)
    finally: await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "retire", "purge"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--after")
    parser.add_argument("--limit", type=int, default=100)
    for name in ("expected-digest", "actor-id", "reason", "batch-id"):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    try:
        if not args.env_file.is_file(): raise CleanupError("configuration_file_missing")
        result = asyncio.run(run(Settings(_env_file=args.env_file), args))
    except Exception as exc:
        print(json.dumps({"status": "refused", "code": str(exc) if isinstance(exc, CleanupError) else "maintenance_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
