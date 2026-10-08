"""Explicit removal of content from already-deleted cloud drafts; no file deletion."""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import Text, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.time import utc_now
from app.db.v2_schema import require_v2_schema
from app.design.models import AdminAction, EditorDraft
from app.maintenance.orphan_assets import CleanupError, database_identity, digest
from app.maintenance.runtime_retention import administrator, receipt
from app.maintenance.upload_retention import lock_registry

SCOPE = "v2_deleted_draft_content"
MAX_CONTENT_BYTES = 16 * 1024 * 1024


async def inspect_drafts(db, *, after=None, limit=25):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise CleanupError("invalid_retention_limit")
    if after is not None:
        try:
            if str(UUID(after)) != after:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise CleanupError("invalid_retention_cursor") from None
    await lock_registry(db)
    query = select(EditorDraft.id, (func.octet_length(cast(EditorDraft.document, Text)) +
                   func.octet_length(EditorDraft.device_id)).label("size")).where(
        EditorDraft.deleted_at.is_not(None),
        or_(cast(EditorDraft.document, JSONB) != {}, EditorDraft.device_id != ""))
    if after:
        query = query.where(EditorDraft.id > after)
    rows = list(await db.execute(query.order_by(EditorDraft.id).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    if sum(row.size for row in rows) > MAX_CONTENT_BYTES:
        raise CleanupError("draft_batch_too_large_reduce_limit")
    candidates = []
    for item in rows:
        row = await db.get(EditorDraft, item.id)
        candidates.append({"draft_id": row.id, "owner_id": row.owner_id, "version": row.version,
            "deleted_at": row.deleted_at.isoformat(), "updated_at": row.updated_at.isoformat(),
            "content_digest": digest({"document": row.document, "device_id": row.device_id})})
    report = {"format": 1, "scope": SCOPE, "database": await database_identity(db),
              "after": after, "limit": limit, "next_after": rows[-1].id if more else None,
              "candidates": candidates}
    report["digest"] = digest(report)
    return report


async def scrub_drafts(db, expected_digest, actor_id, reason, **kwargs):
    report = await inspect_drafts(db, **kwargs)
    await administrator(db, actor_id, reason)
    request = digest({"action": "scrub_deleted_drafts", "digest": expected_digest, "reason": reason.strip()})
    previous = await db.scalar(select(AdminAction).where(
        AdminAction.actor_id == actor_id, AdminAction.client_request_id == request))
    if previous:
        return {"receipt_id": previous.id, **previous.result}
    if not expected_digest or expected_digest != report["digest"]:
        raise CleanupError("inventory_changed")
    identities = [item["draft_id"] for item in report["candidates"]]
    if identities:
        # Keep tombstones and every write receipt: delayed clients cannot resurrect
        # the old identity, and lost acknowledgements retain their original result.
        await db.execute(update(EditorDraft).where(EditorDraft.id.in_(identities),
            EditorDraft.deleted_at.is_not(None)).values(document={}, device_id=""))
    return await receipt(db, actor_id, reason, "scrub_deleted_drafts", request,
        {"status": "scrubbed", "count": len(identities), "manifest": report}, utc_now())


async def run(settings, args):
    if not settings.v2_enabled:
        raise CleanupError("v2_required")
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            options = {"after": args.after, "limit": args.limit}
            if args.command == "inspect":
                return await inspect_drafts(db, **options)
            return await scrub_drafts(db, args.expected_digest, args.actor_id, args.reason, **options)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "apply"])
    parser.add_argument("--env-file", type=Path, required=True)
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
                          else "draft_cleanup_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
