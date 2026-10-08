"""Explicit retirement of native histories used only by irrecoverable designs."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
from pathlib import Path

from sqlalchemy import JSON, String, Text, cast, column, func, or_, select, text, values
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assets.repository import storage_io
from app.core.config import Settings
from app.core.time import utc_now
from app.db.base import Base
from app.db.v2_schema import require_v2_schema
from app.design.models import AdminAction, Branch, CodexEpoch, Design, EditorDraft, Operation
from app.maintenance import native_history_store as native, runtime_retention as runtime
from app.maintenance.orphan_assets import CleanupError, database_identity, digest
from app.maintenance.runtime_intermediates import environment as runtime_environment

SCOPE = "v2_expired_native_history"
RETIRE = "retire_native_history"
PURGE = "purge_native_history"


def expired(now):
    return select(Design.id).where(Design.deleted_at.is_not(None),
        Design.trash_restore_until <= now, Design.content_purged_at.is_not(None))


def allowed_scope(table, now):
    c = table.c
    designs = expired(now)
    branches = select(Branch.id).where(Branch.design_id.in_(designs))
    operations = select(Operation.id).where(Operation.design_id.in_(designs))
    if table.name == Design.__tablename__: return c.id.in_(designs)
    if "design_id" in c: return c.design_id.in_(designs)
    if "branch_id" in c: return c.branch_id.in_(branches)
    if "operation_id" in c: return c.operation_id.in_(operations)
    if "source_operation_id" in c: return c.source_operation_id.in_(operations)
    if "draft_id" in c: return c.draft_id.in_(select(EditorDraft.id).where(EditorDraft.design_id.in_(designs)))
    return None


async def protected_threads(db, ids, now):
    if not ids: return set()
    wanted = values(column("thread", String), name="native_ids").data([(key,) for key in sorted(ids)])
    protected = set()
    from app.maintenance.account_content import retired_scopes
    retired = retired_scopes()
    for table in Base.metadata.tables.values():
        if table.name == AdminAction.__tablename__: continue
        columns = [col for col in table.columns if isinstance(col.type, (String, Text, JSON))]
        if not columns: continue
        query = select(wanted.c.thread).select_from(wanted.join(table,
            or_(*(func.strpos(func.lower(cast(col, Text)), wanted.c.thread) > 0 for col in columns))))
        allowed = allowed_scope(table, now)
        if table.name in retired: allowed = retired[table.name] if allowed is None else or_(allowed, retired[table.name])
        if allowed is not None: query = query.where(func.coalesce(allowed, False).is_(False))
        protected.update(await db.scalars(query.distinct()))
    return protected


async def eligible(db, now, *, owner=None):
    from app.maintenance.container_backup import owned_threads
    all_threads = await owned_threads(db, include_retired=True)
    # User-editable JSON may mention an unrelated personal thread. Only threads
    # registered by an epoch or dispatched operation are deletion candidates.
    registered = set(await db.scalars(select(CodexEpoch.thread_id)))
    registered.update(await db.scalars(select(Operation.upstream_thread_id).where(Operation.upstream_thread_id.is_not(None))))
    selected = {key: account for key, account in all_threads.items()
                if key in registered and (owner is None or account == owner)}
    # Bounded SQL VALUES batches, including unknown textual/JSON references.
    result = dict(selected)
    keys = sorted(selected)
    for offset in range(0, len(keys), 100):
        for key in await protected_threads(db, keys[offset:offset + 100], now): result.pop(key, None)
    return result


def manifest(row):
    if row is None or row.action != RETIRE or row.status != "completed":
        raise CleanupError("native_retirement_receipt_required")
    value = (row.result or {}).get("manifest")
    if (not isinstance(value, dict) or value.get("scope") != SCOPE or value.get("format") != 1
        or value.get("digest") != digest({key: item for key, item in value.items() if key != "digest"})
        or type(value.get("grace_days")) is not int or not 1 <= value["grace_days"] <= 3650):
        raise CleanupError("invalid_native_retirement_manifest")
    ids = value.get("threads")
    if not isinstance(ids, dict) or not 1 <= len(ids) <= 100 or not all(runtime.identity(key) and runtime.identity(owner) for key, owner in ids.items()):
        raise CleanupError("invalid_native_retirement_manifest")
    return value


async def retired_threads(db, now=None):
    """Backup tombstones survive isolated restore; current business refs are rechecked."""
    rows = list(await db.scalars(select(AdminAction).where(AdminAction.action == PURGE,
        AdminAction.status == "completed").limit(10001)))
    if len(rows) > 10000: raise CleanupError("native_receipt_inventory_too_large")
    result = {}
    for row in rows:
        value = manifest(await db.get(AdminAction, (row.result or {}).get("batch_id")))
        if row.result.get("manifest_digest") != value["digest"]: raise CleanupError("invalid_native_purge_receipt")
        for key in value["threads"]: result[key] = row.id
    if result:
        allowed = await eligible(db, now or utc_now())
        if not set(result) <= set(allowed): raise CleanupError("retired_native_reference_added")
    return result


@asynccontextmanager
async def environment(db, settings, writers_stopped, profile):
    if not settings.v2_native_history_cleanup_enabled: raise CleanupError("native_history_cleanup_disabled")
    async with runtime_environment(db, settings, writers_stopped, profile) as bound:
        # runtime guard protects in-flight work globally; also reject unresolved context.
        pending = await db.scalar(select(Operation.id).where(
            Operation.context_allocation["state"].as_string().in_(["sent", "unknown"])).limit(1))
        if pending: raise CleanupError("operation_unconfirmed")
        yield bound


async def inspect_locked(db, settings, native_profile, now, after, limit):
    if (after is not None and not runtime.identity(after)) or type(limit) is not int or not 1 <= limit <= 100:
        raise CleanupError("invalid_retention_page")
    owner = settings.v2_worker_user_id if settings.v2_runtime_mode == "container" else None
    candidates = await eligible(db, now, owner=owner)
    already = await retired_threads(db, now)
    keys = [key for key in sorted(candidates) if key not in already and (after is None or key > after)]
    page = keys[:limit]
    if not page: raise CleanupError("no_eligible_native_history")
    proof, _ = await storage_io(native.inspect_store, native_profile, page, settings.v2_codex_version)
    report = {"format": 1, "scope": SCOPE, "database": await database_identity(db),
        "native_profile": str(native_profile), "runtime_mode": settings.v2_runtime_mode, "owner_id": owner,
        "codex_version": settings.v2_codex_version, "grace_days": settings.v2_native_history_grace_days,
        "threads": {key: candidates[key] for key in page}, "native": proof,
        "after": after, "limit": limit, "next_after": page[-1] if len(keys) > limit else None}
    report["digest"] = digest(report)
    return report


async def inspect_history(db, settings, *, writers_stopped=False, profile=None, now=None, after=None, limit=100):
    async with environment(db, settings, writers_stopped, profile) as (_, _, _, home):
        return await inspect_locked(db, settings, home, now or utc_now(), after, limit)


async def retire_history(db, settings, expected_digest, actor_id, reason, *,
                         writers_stopped=False, profile=None, now=None, after=None, limit=100):
    async with environment(db, settings, writers_stopped, profile) as (_, _, _, home):
        await runtime.administrator(db, actor_id, reason)
        request = digest({"action": RETIRE, "digest": expected_digest, "reason": reason.strip()})
        existing = await db.scalar(select(AdminAction).where(AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
        if existing: return {"receipt_id": existing.id, **existing.result}
        report = await inspect_locked(db, settings, home, now or utc_now(), after, limit)
        if report["digest"] != expected_digest: raise CleanupError("inventory_changed")
        return await runtime.receipt(db, actor_id, reason, RETIRE, request,
            {"status": "retired", "manifest": report, "count": len(report["threads"])}, now or utc_now())


async def purge_history(db, settings, batch_id, actor_id, reason, *,
                        writers_stopped=False, profile=None, now=None):
    async with environment(db, settings, writers_stopped, profile) as (_, _, container, home):
        await runtime.administrator(db, actor_id, reason)
        row = await db.get(AdminAction, batch_id)
        value = manifest(row)
        owner = settings.v2_worker_user_id if settings.v2_runtime_mode == "container" else None
        expected = {"database": await database_identity(db), "native_profile": str(home),
            "runtime_mode": settings.v2_runtime_mode, "owner_id": owner, "codex_version": settings.v2_codex_version}
        if any(value.get(key) != item for key, item in expected.items()): raise CleanupError("native_retirement_scope_mismatch")
        if await db.scalar(text("SELECT xmin::text::bigint = txid_current() % 4294967296 FROM v2_admin_actions WHERE id=:id"), {"id": batch_id}):
            raise CleanupError("retirement_commit_required")
        now = now or utc_now()
        if row.completed_at is None or now < row.completed_at + timedelta(days=max(value["grace_days"], settings.v2_native_history_grace_days)):
            raise CleanupError("retirement_grace_period")
        allowed = await eligible(db, now, owner=owner)
        if any(allowed.get(key) != account for key, account in value["threads"].items()):
            raise CleanupError("native_reference_added")
        request = digest({"action": PURGE, "batch_id": batch_id, "reason": reason.strip()})
        existing = await db.scalar(select(AdminAction).where(AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
        if existing: return {"receipt_id": existing.id, **existing.result}
        if container is not None: await container.verify_stopped()
        await storage_io(native.purge_store, home, sorted(value["threads"]), settings.v2_codex_version, value["native"])
        return await runtime.receipt(db, actor_id, reason, PURGE, request,
            {"status": "purged", "batch_id": batch_id, "manifest_digest": value["digest"], "count": len(value["threads"])}, now)


async def run(settings, args):
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            options = {"writers_stopped": args.writers_stopped}
            if args.command == "inspect": return await inspect_history(db, settings, after=args.after, limit=args.limit, **options)
            if args.command == "retire": return await retire_history(db, settings, args.expected_digest, args.actor_id, args.reason, after=args.after, limit=args.limit, **options)
            return await purge_history(db, settings, args.batch_id, args.actor_id, args.reason, **options)
    finally: await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "retire", "purge"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    for name in ("after", "expected-digest", "actor-id", "reason", "batch-id"): parser.add_argument("--" + name)
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
