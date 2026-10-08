"""Explicit transactional redaction of expired design content; no file or ledger deletion."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re

from sqlalchemy import JSON, String, Text, and_, cast, func, literal, or_, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.time import utc_now
from app.db.base import Base
from app.db.v2_schema import require_v2_schema
from app.design.models import (AdminAction, Asset, Branch, Design, DomainEvent, EditorCheckpoint,
    EditorDraft, InputRecord, LegacyImport, Message, Operation, Revision, ViewSet)
from app.maintenance.backup_bundle import thread_references
from app.maintenance.orphan_assets import CleanupError, database_identity, digest
from app.maintenance.remote_assets import paused
from app.maintenance.runtime_retention import administrator, identity, receipt
from app.maintenance.upload_retention import lock_registry
from app.operations.service import TERMINAL

SCOPE = "v2_expired_design_content"
ACTION = "scrub_design_content"
MAX_ROWS = 10000
MAX_BYTES = 16 * 1024 * 1024
UUID_TOKEN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
CONTENT = {
    Branch: {"context_seed": None},
    InputRecord: {"snapshot": {}},
    Operation: {"question": None, "context_before": None, "context_allocation": None},
    Revision: {"recipe": None, "context_checkpoint": None},
    Message: {"content_parts": [], "search_text": ""},
    DomainEvent: {"payload": {}},
    EditorDraft: {"document": {}, "device_id": ""},
    EditorCheckpoint: {"document": {}},
    LegacyImport: {"source_summary": {}},
    ViewSet: {"snapshot": {}},
}
# Only exact content guards are suspended, transactionally under exclusive table
# locks. Financial, identity, history-order and idempotency guards stay enabled.
CONTENT_TRIGGERS = {
    "v2_input_snapshots": "v2_input_snapshots_immutable",
    "v2_revisions": "v2_revisions_immutable",
    "v2_editor_checkpoints": "v2_editor_checkpoints_immutable",
    "v2_legacy_imports": "v2_legacy_import_guard",
    "v2_viewsets": "v2_viewset_fixed_input",
}


def scopes(design_id):
    branches = select(Branch.id).where(Branch.design_id == design_id)
    operations = select(Operation.id).where(Operation.design_id == design_id)
    drafts = select(EditorDraft.id).where(EditorDraft.design_id == design_id)
    result = {}
    for table in Base.metadata.tables.values():
        c = table.c
        if table.name == Design.__tablename__: result[table.name] = c.id == design_id
        elif "design_id" in c: result[table.name] = c.design_id == design_id
        elif "branch_id" in c: result[table.name] = c.branch_id.in_(branches)
        elif "operation_id" in c: result[table.name] = c.operation_id.in_(operations)
        elif "source_operation_id" in c: result[table.name] = c.source_operation_id.in_(operations)
        elif "draft_id" in c: result[table.name] = c.draft_id.in_(drafts)
    return result


async def guard(db, settings, writers_stopped):
    if not settings.v2_design_content_cleanup_enabled:
        raise CleanupError("design_content_cleanup_disabled")
    await lock_registry(db)
    await paused(db, settings, writers_stopped)
    pending = await db.scalar(select(Operation.id).where(or_(
        Operation.state.not_in(TERMINAL), Operation.reservation_closed.is_(False),
        and_(Operation.dispatch_intent_at.is_not(None), or_(
            Operation.upstream_status.is_(None),
            Operation.upstream_status.not_in(["completed", "failed", "interrupted"]))),
        Operation.context_allocation["state"].as_string().in_(["sent", "unknown"]),
    )).limit(1))
    if pending:
        raise CleanupError("operation_unconfirmed")
    if await db.scalar(select(ViewSet.id).where(or_(
        ViewSet.held_units != 0, ViewSet.state.not_in(["succeeded", "partial", "cancelled", "failed"]))).limit(1)):
        raise CleanupError("viewset_unconfirmed")


def serialize(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                      default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item))


async def content_inventory(db, design_id):
    conditions = scopes(design_id)
    records = {}
    total = count = 0
    for model in (Design, *CONTENT):
        table = model.__table__
        columns = [col for col in table.c if isinstance(col.type, (JSON, String, Text))]
        size = sum((func.coalesce(func.octet_length(cast(col, Text)), 0) for col in columns), literal(0))
        total += await db.scalar(select(func.coalesce(func.sum(size), 0)).where(conditions[table.name])) or 0
        if total > MAX_BYTES: raise CleanupError("design_content_batch_too_large")
        rows = (await db.execute(select(table).where(conditions[table.name])
            .order_by(*table.primary_key.columns).limit(MAX_ROWS + 1))).mappings().all()
        count += len(rows)
        if count > MAX_ROWS: raise CleanupError("design_content_batch_too_large")
        records[table.name] = [dict(row) for row in rows]
    encoded = serialize(records)
    if len(encoded.encode()) > MAX_BYTES: raise CleanupError("design_content_batch_too_large")
    return records, encoded, conditions


async def externally_referenced(db, records, conditions):
    # Protect content-bearing identities used by other designs, legacy data or
    # unknown consumers. Ledger/audit links only require tombstone identities.
    ids = {row["id"] for rows in records.values() for row in rows if "id" in row}
    skip = {AdminAction.__tablename__, "v2_ledger"}
    # After irreversible account closure, its legacy records cannot be restored.
    # They must not create a cycle with design_content -> account_content cleanup.
    from app.design.models import AccountClosure
    from app.maintenance.account_content import scopes as account_scopes
    owner = records[Design.__tablename__][0]["owner_id"]
    closed = await db.get(AccountClosure, owner)
    closed_conditions = account_scopes(owner) if closed and closed.state == "closed" else {}

    # A SQL array avoids bringing any other user's text into the maintenance report.
    from sqlalchemy import column, values
    wanted = values(column("id", String), name="content_ids").data([(key,) for key in sorted(ids)])
    for table in sorted(Base.metadata.tables.values(), key=lambda value: value.name):
        if table.name in skip: continue
        columns = [col for col in table.c if isinstance(col.type, (String, Text, JSON))]
        if not columns: continue
        matches = or_(*(func.strpos(func.lower(cast(col, Text)), wanted.c.id) > 0 for col in columns))
        query = select(literal(True)).select_from(wanted.join(table, matches))
        allowed = conditions.get(table.name)
        if table.name in closed_conditions:
            allowed = closed_conditions[table.name] if allowed is None else or_(allowed, closed_conditions[table.name])
        if allowed is not None:
            query = query.where(~func.coalesce(allowed, False))
        if await db.scalar(query.limit(1)): return True
    return False


async def resource_references(db, records, encoded):
    ids = sorted(set(UUID_TOKEN.findall(encoded.lower())))
    assets = list(await db.scalars(select(Asset.id).where(or_(
        Asset.id.in_(ids), func.strpos(literal(encoded.lower()), func.lower(Asset.object_key)) > 0)).order_by(Asset.id).limit(MAX_ROWS + 1)))
    threads = set()
    for rows in records.values():
        for row in rows:
            for value in row.values():
                if isinstance(value, (dict, list)): threads.update(thread_references(value))
    if len(assets) > MAX_ROWS or any(not identity(thread) for thread in threads):
        raise CleanupError("unverifiable_retained_resources")
    return {"asset_ids": assets, "native_threads": [{"thread_id": key} for key in sorted(threads)]}


async def inspect_designs(db, settings, *, writers_stopped=False, now=None, after=None, limit=10):
    if (after is not None and not identity(after)) or type(limit) is not int or not 1 <= limit <= 25:
        raise CleanupError("invalid_retention_page")
    await guard(db, settings, writers_stopped)
    boundary = (now or utc_now()).replace(hour=0, minute=0, second=0, microsecond=0)
    query = select(Design).where(Design.deleted_at.is_not(None), Design.trash_restore_until <= boundary,
                                 Design.content_purged_at.is_(None))
    if after: query = query.where(Design.id > after)
    rows = list(await db.scalars(query.order_by(Design.id).limit(limit + 1)))
    more, rows = len(rows) > limit, rows[:limit]
    candidates, retained = [], []
    for design in rows:
        records, encoded, conditions = await content_inventory(db, design.id)
        if await externally_referenced(db, records, conditions):
            retained.append({"design_id": design.id, "reason": "shared_or_unknown_content_reference"})
            continue
        candidates.append({"design_id": design.id, "owner_id": design.owner_id,
            "content_digest": digest(json.loads(encoded)), "counts": {key: len(value) for key, value in records.items()},
            "retained_resources": await resource_references(db, records, encoded)})
    report = {"format": 1, "scope": SCOPE, "database": await database_identity(db), "cutoff": boundary.isoformat(),
        "after": after, "limit": limit, "next_after": rows[-1].id if more else None,
        "candidates": candidates, "retained": retained}
    report["digest"] = digest(report)
    return report


async def scrub_designs(db, settings, expected_digest, actor_id, reason, **kwargs):
    report = await inspect_designs(db, settings, **kwargs)
    await administrator(db, actor_id, reason)
    request = digest({"action": ACTION, "digest": expected_digest, "reason": reason.strip()})
    previous = await db.scalar(select(AdminAction).where(AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
    if previous: return {"receipt_id": previous.id, **previous.result}
    if not expected_digest or expected_digest != report["digest"]: raise CleanupError("inventory_changed")
    now = kwargs.get("now") or utc_now()
    # Savepoint restores both data and trigger states even when an embedding
    # caller catches an error and commits its surrounding transaction.
    async with db.begin_nested():
        for table, trigger in CONTENT_TRIGGERS.items():
            enabled = await db.scalar(text("SELECT tgenabled::text FROM pg_trigger WHERE tgrelid=to_regclass(:table) AND tgname=:name"),
                                      {"table": table, "name": trigger})
            if enabled != "O": raise CleanupError("content_guard_unavailable")
        for table, trigger in CONTENT_TRIGGERS.items():
            await db.execute(text(f'ALTER TABLE "{table}" DISABLE TRIGGER "{trigger}"'))
        for item in report["candidates"]:
            conditions = scopes(item["design_id"])
            for model, fields in CONTENT.items():
                values = dict(fields)
                if model is EditorDraft: values["deleted_at"] = func.coalesce(EditorDraft.deleted_at, now)
                await db.execute(update(model).where(conditions[model.__tablename__]).values(**values)
                                 .execution_options(synchronize_session=False))
            await db.execute(update(Design).where(Design.id == item["design_id"]).values(
                title="", pinned=False, content_purged_at=now, retained_resources=item["retained_resources"],
                metadata_version=Design.metadata_version + 1).execution_options(synchronize_session=False))
        for table, trigger in CONTENT_TRIGGERS.items():
            await db.execute(text(f'ALTER TABLE "{table}" ENABLE TRIGGER "{trigger}"'))
        return await receipt(db, actor_id, reason, ACTION, request,
            {"status": "scrubbed", "count": len(report["candidates"]), "manifest": report}, now)


async def run(settings, args):
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            options = {"writers_stopped": args.writers_stopped, "after": args.after, "limit": args.limit}
            if args.command == "inspect": return await inspect_designs(db, settings, **options)
            return await scrub_designs(db, settings, args.expected_digest, args.actor_id, args.reason, **options)
    finally: await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "apply"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--after")
    parser.add_argument("--limit", type=int, default=10)
    for name in ("expected-digest", "actor-id", "reason"): parser.add_argument("--" + name)
    args = parser.parse_args()
    try:
        if not args.env_file.is_file(): raise CleanupError("configuration_file_missing")
        result = asyncio.run(run(Settings(_env_file=args.env_file), args))
    except Exception as exc:
        print(json.dumps({"status": "refused", "code": str(exc) if isinstance(exc, CleanupError) else "content_cleanup_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
