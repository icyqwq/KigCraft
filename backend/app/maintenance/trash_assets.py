"""Expire assets used only by elapsed trash, then purge after an explicit grace.

The original designs, audit, native history and backups remain intact. Unknown
reference types protect bytes. No schedule; disabled until explicitly configured.
"""
import argparse
import asyncio
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import JSON, String, Text, and_, cast, false, func, or_, select, text
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.assets.repository import storage_io
from app.maintenance.remote_assets import RemoteAssets, deployment_allowed
from app.core.config import Settings
from app.core.time import utc_now
from app.db.base import Base
from app.db.v2_schema import require_v2_schema
from app.design.models import (AdminAction, Asset, Branch, CodexEpoch, Design, DomainEvent,
    EditorCheckpoint, EditorDraft, InputRecord, LegacyImport, Message, Operation, Revision, RuntimeControl,
    ViewSet, ViewSetItem)
from app.maintenance.orphan_assets import CleanupError, MAX_BATCH_BYTES, checked, database_identity, digest, fingerprint, root_path
from app.maintenance.upload_retention import lock_registry
from app.maintenance.runtime_retention import administrator, receipt
from app.operations.service import TERMINAL

SCOPE = "v2_expired_trash_assets"
DIRECT = {model.__tablename__ for model in
          (Branch, DomainEvent, EditorCheckpoint, EditorDraft, LegacyImport, Operation, Revision, ViewSet, ViewSetItem)}
BRANCHED = {model.__tablename__ for model in (CodexEpoch, InputRecord, Message)}


def cutoff(now):
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def references(db, assets, boundary):
    from app.maintenance.account_content import retired_scopes
    from app.design.models import AccountContentRetirement, AccountClosure, LegacyMaterial
    retired = retired_scopes(Asset.owner_id)
    identities = [asset.id for asset in assets]
    used, protected = set(), set()
    if not identities:
        return used, protected
    used.update(await db.scalars(select(Asset.id).where(Asset.id.in_(identities),
        Asset.owner_id.in_(select(AccountContentRetirement.owner_id).join(AccountClosure,
            AccountClosure.owner_id == AccountContentRetirement.owner_id).where(AccountClosure.state == "closed")))))
    d, b = aliased(Design), aliased(Branch)
    eligible = (d.deleted_at.is_not(None), d.trash_restore_until.is_not(None),
                d.trash_restore_until <= boundary, d.owner_id == Asset.owner_id)
    designs = select(d.id).where(*eligible).correlate(Asset)
    branches = select(b.id).join(d, b.design_id == d.id).where(*eligible).correlate(Asset)
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        if table.name in {Asset.__tablename__, AdminAction.__tablename__}:
            continue
        columns = [column for column in table.columns if isinstance(column.type, (String, Text, JSON))]
        if not columns:
            continue
        matches = or_(*(or_(func.strpos(func.lower(cast(column, Text)), func.lower(Asset.id)) > 0,
                              func.strpos(func.lower(cast(column, Text)), func.lower(Asset.object_key)) > 0)
                        for column in columns))
        allowed = false()
        if table.name == Design.__tablename__:
            allowed = table.c.id.in_(designs)
        elif table.name in DIRECT:
            allowed = table.c.design_id.in_(designs)
        elif table.name in BRANCHED:
            allowed = table.c.branch_id.in_(branches)
        if table.name in retired: allowed = or_(allowed, retired[table.name])
        query = select(Asset.id).select_from(Asset.__table__.join(table, matches)).where(Asset.id.in_(identities)).distinct()
        used.update(await db.scalars(query.where(allowed)))
        # Coalesce null scope links to false: unclassified consumers retain data.
        protected.update(await db.scalars(query.where(~func.coalesce(allowed, False))))
    return used, protected


async def guard(db, settings, stopped):
    if not stopped:
        raise CleanupError("writers_stopped_acknowledgement_required")
    if not deployment_allowed(settings):
        raise CleanupError("maintenance_configuration_required")
    if not settings.v2_trash_asset_cleanup_enabled:
        raise CleanupError("trash_asset_cleanup_disabled")
    root = RemoteAssets(settings) if settings.v2_asset_backend == "minio" else root_path(Path(settings.v2_storage_dir))
    await lock_registry(db)
    control = await db.get(RuntimeControl, "global")
    if control is None or not control.blocked:
        raise CleanupError("generation_must_be_paused")
    if await db.scalar(select(AdminAction.id).where(AdminAction.status.in_(["pending", "running"])).limit(1)):
        raise CleanupError("admin_action_pending")
    if await db.scalar(select(Operation.id).where(or_(
        Operation.state.not_in(TERMINAL), Operation.reservation_closed.is_(False),
        Operation.lease_owner.is_not(None), Operation.lease_expires_at.is_not(None),
        and_(Operation.dispatch_intent_at.is_not(None), or_(Operation.upstream_status.is_(None),
            Operation.upstream_status.not_in(["completed", "failed", "interrupted"]))),
        Operation.context_allocation["state"].as_string().in_(["sent", "unknown"]),
    )).limit(1)):
        raise CleanupError("operation_unconfirmed")
    return root


def supported_key(asset):
    if asset.object_key in {f"uploads/{asset.id}.png", f"masters/{asset.id}.png", f"uploads/{asset.id}.jpg", f"masters/{asset.id}.jpg"}:
        return True
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(asset.mime_type)
    return asset.kind in {"legacy", "legacy_reference"} and extension is not None and asset.object_key == f"legacy/{asset.id}{extension}"


async def legacy_mapping(db, asset):
    if not asset.object_key.startswith("legacy/"):
        return True
    if asset.kind == "legacy_reference":
        from app.design.models import LegacyMaterial, AccountContentRetirement
        return bool(await db.scalar(select(LegacyMaterial.id).where(
            LegacyMaterial.asset_id == asset.id, LegacyMaterial.owner_id == asset.owner_id,
            LegacyMaterial.owner_id.in_(select(AccountContentRetirement.owner_id)),
            LegacyMaterial.source_kind.in_(["reference","project_reference"])).limit(1)))
    return bool(await db.scalar(select(LegacyImport.id).join(Revision, Revision.id == LegacyImport.revision_id).where(
        LegacyImport.owner_id == asset.owner_id, LegacyImport.image_sha256 == asset.sha256,
        LegacyImport.source_kind.in_(["output", "album"]), Revision.clean_asset_id == asset.id,
        Revision.root_source_asset_id == asset.id, Revision.source_kind == "legacy").limit(1)))


async def retired_asset(db, asset):
    """Only a committed, content-bound expiry receipt authorizes retired replay."""
    from sqlalchemy.dialects.postgresql import JSONB
    if asset.status not in {"expired", "purged"}:
        return False
    expected = {"asset_id": asset.id, "key": asset.object_key, "sha256": asset.sha256, "bytes": asset.byte_size}
    rows = list(await db.scalars(select(AdminAction).where(
        AdminAction.action == "expire_trash_assets", AdminAction.status == "completed",
        cast(AdminAction.result, JSONB).contains({"manifest": {"candidates": [expected]}})
    ).limit(101)))
    if len(rows) > 100:
        raise CleanupError("asset_retirement_history_too_large")
    for row in rows:
        report = row.result.get("manifest")
        if (not isinstance(report, dict) or report.get("scope") != SCOPE or report.get("format") != 1
                or report.get("digest") != digest({k:v for k,v in report.items() if k != "digest"})):
            raise CleanupError("invalid_expiry_manifest")
        if expected not in report["candidates"]:
            continue
        uncommitted = await db.scalar(text(
            "SELECT xmin::text::bigint = txid_current() % 4294967296 FROM v2_admin_actions WHERE id=:id"), {"id":row.id})
        if not uncommitted:
            return True
    return False


def file_matches(root, asset, *, missing=False):
    if not supported_key(asset):
        raise CleanupError("unsupported_trash_asset_key")
    path = checked(root, asset.object_key)
    if not path.exists() and missing:
        return path
    if path.stat().st_nlink != 1 or fingerprint(path) != {"sha256": asset.sha256, "bytes": asset.byte_size}:
        raise CleanupError("trash_asset_content_changed")
    return path


async def asset_matches(root, asset, *, missing=False):
    if not supported_key(asset):
        raise CleanupError("unsupported_trash_asset_key")
    if isinstance(root, RemoteAssets):
        return await storage_io(root.inspect, asset.object_key, asset.sha256, asset.byte_size, missing=missing)
    file_matches(root, asset, missing=missing)
    return None


async def inspect_assets(db, settings, *, writers_stopped=False, now=None, after=None, limit=100):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise CleanupError("invalid_retention_limit")
    if after is not None and str(UUID(after)) != after:
        raise CleanupError("invalid_retention_cursor")
    root = await guard(db, settings, writers_stopped)
    boundary = cutoff(now or utc_now())
    query = select(Asset).where(Asset.status == "ready")
    if after:
        query = query.where(Asset.id > after)
    assets = list(await db.scalars(query.order_by(Asset.id).limit(limit+1)))
    more = len(assets) > limit
    assets = assets[:limit]
    used, protected = await references(db, assets, boundary)
    candidates, retained = [], []
    remote_objects = {}
    total = 0
    for asset in assets:
        if asset.id not in used or asset.id in protected:
            retained.append({"asset_id": asset.id, "reason": "live_or_unknown_reference" if asset.id in protected else "not_expired_trash"})
            continue
        if not supported_key(asset) or not await legacy_mapping(db, asset):
            retained.append({"asset_id": asset.id, "reason": "unsupported_key"})
            continue
        observed = await asset_matches(root, asset)
        if observed is not None:
            remote_objects[asset.id] = observed
        total += asset.byte_size
        if total > MAX_BATCH_BYTES:
            raise CleanupError("retention_batch_too_large")
        candidates.append({"asset_id": asset.id, "key": asset.object_key, "sha256": asset.sha256, "bytes": asset.byte_size})
    report = {"format": 1, "scope": SCOPE, "database": await database_identity(db),
        "storage_root": str(root), "cutoff": boundary.isoformat(), "grace_days": settings.v2_trash_asset_grace_days,
        "after": after, "limit": limit, "next_after": assets[-1].id if more else None,
        "candidates": candidates, "retained": retained}
    if isinstance(root, RemoteAssets):
        report["remote_objects"] = remote_objects
    report["digest"] = digest(report)
    return report


async def expire_assets(db, settings, expected_digest, actor, reason, **kwargs):
    report = await inspect_assets(db, settings, **kwargs)
    await administrator(db, actor, reason)
    request = digest({"action": "expire_trash_assets", "digest": expected_digest, "reason": reason.strip()})
    previous = await db.scalar(select(AdminAction).where(AdminAction.actor_id == actor, AdminAction.client_request_id == request))
    if previous:
        return {"receipt_id": previous.id, **previous.result}
    if report["digest"] != expected_digest:
        raise CleanupError("inventory_changed")
    for item in report["candidates"]:
        (await db.get(Asset, item["asset_id"])).status = "expired"
    return await receipt(db, actor, reason, "expire_trash_assets", request,
        {"status": "expired", "manifest": report, "count": len(report["candidates"])}, kwargs.get("now") or utc_now())


async def purge_assets(db, settings, batch_id, actor, reason, *, writers_stopped=False, now=None):
    root = await guard(db, settings, writers_stopped)
    await administrator(db, actor, reason)
    row = await db.get(AdminAction, batch_id)
    if row is None or row.action != "expire_trash_assets" or row.status != "completed":
        raise CleanupError("expiry_receipt_required")
    report = row.result.get("manifest")
    if (not isinstance(report, dict) or report.get("scope") != SCOPE or report.get("format") != 1
            or report.get("digest") != digest({k:v for k,v in report.items() if k != "digest"})):
        raise CleanupError("invalid_expiry_manifest")
    if report["storage_root"] != str(root) or report["database"] != await database_identity(db):
        raise CleanupError("expiry_scope_mismatch")
    if await db.scalar(text("SELECT xmin::text::bigint = txid_current() % 4294967296 FROM v2_admin_actions WHERE id=:id"), {"id": batch_id}):
        raise CleanupError("expiry_commit_required")
    request = digest({"action": "purge_trash_assets", "batch_id": batch_id, "reason": reason.strip()})
    previous = await db.scalar(select(AdminAction).where(AdminAction.actor_id == actor, AdminAction.client_request_id == request))
    if previous:
        return {"receipt_id": previous.id, **previous.result}
    now = now or utc_now()
    days = max(report["grace_days"], settings.v2_trash_asset_grace_days)
    if row.completed_at is None or now < row.completed_at + timedelta(days=days):
        raise CleanupError("trash_asset_grace_period")
    assets = []
    for item in report["candidates"]:
        asset = await db.get(Asset, item["asset_id"])
        if (asset is None or asset.status not in {"expired", "purged"}
                or {"asset_id": asset.id, "key": asset.object_key, "sha256": asset.sha256, "bytes": asset.byte_size} != item):
            raise CleanupError("trash_asset_changed")
        if not await legacy_mapping(db, asset):
            raise CleanupError("legacy_asset_mapping_changed")
        assets.append(asset)
    used, protected = await references(db, assets, cutoff(now))
    if any(asset.id not in used or asset.id in protected for asset in assets):
        raise CleanupError("trash_asset_referenced")
    for asset in assets:
        await asset_matches(root, asset, missing=True)
    for asset in assets:
        if isinstance(root, RemoteAssets):
            await storage_io(root.delete, asset.object_key, asset.sha256, asset.byte_size)
        else:
            path = file_matches(root, asset, missing=True)
            if path.exists():
                path.unlink()
        asset.status = "purged"
    return await receipt(db, actor, reason, "purge_trash_assets", request,
        {"status": "purged", "batch_id": batch_id, "count": len(assets), "manifest_digest": report["digest"]}, now)


async def run(settings, args):
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            if args.command == "inspect":
                return await inspect_assets(db, settings, writers_stopped=args.writers_stopped, after=args.after, limit=args.limit)
            if args.command == "expire":
                return await expire_assets(db, settings, args.expected_digest, args.actor_id, args.reason,
                    writers_stopped=args.writers_stopped, after=args.after, limit=args.limit)
            return await purge_assets(db, settings, args.batch_id, args.actor_id, args.reason, writers_stopped=args.writers_stopped)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "expire", "purge"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--expected-digest")
    parser.add_argument("--batch-id")
    parser.add_argument("--actor-id")
    parser.add_argument("--reason")
    parser.add_argument("--after")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise CleanupError("configuration_file_missing")
        result = asyncio.run(run(Settings(_env_file=args.env_file), args))
    except Exception as exc:
        print(json.dumps({"status": "refused", "code": str(exc) if isinstance(exc, CleanupError) else "trash_asset_cleanup_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
