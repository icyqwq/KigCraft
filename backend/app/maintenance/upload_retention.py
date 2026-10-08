"""Explicit expiry of unused uploads. Disabled until a retention policy is configured.

Formal assets, history, trash, native threads and backups are outside this policy.
Run inspect, review its digest, then expire. Purge is a separate grace-period action.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import JSON, String, Text, and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assets.repository import storage_io
from app.maintenance.remote_assets import RemoteAssets, deployment_allowed, paused
from app.auth.models import UserRole
from app.core.config import Settings
from app.core.time import utc_now
from app.db.base import Base
from app.db.models import User
from app.db.v2_schema import V2_SCHEMA_VERSION, require_v2_schema
from app.design.models import AdminAction, Asset, Operation
from app.maintenance.orphan_assets import (
    CleanupError, MAX_BATCH_BYTES, OBJECT, checked, database_identity, digest,
    fingerprint, maintenance_lock, root_path,
)
from app.operations.service import TERMINAL

MAX_BATCH = 100
MAX_RECEIPTS = 10000
ACTIONS = {"expire", "purge", "restore"}


async def lock_registry(db):
    """Exclude transactions that could validate an asset then add a JSON reference.

    NOWAIT refuses busy installations instead of introducing a maintenance lock queue.
    Asset ACCESS EXCLUSIVE also waits out existing readers before retirement.
    """
    await maintenance_lock(db)
    await db.execute(text("SET LOCAL statement_timeout = '15s'"))
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(7272192201)"))
    if await db.scalar(text("SELECT version_num FROM public.alembic_version")) != V2_SCHEMA_VERSION:
        raise CleanupError("schema_changed")
    await db.execute(text('LOCK TABLE "v2_assets" IN ACCESS EXCLUSIVE MODE NOWAIT'))
    # All application tables, including legacy opaque JSON, participate. No row
    # contents are loaded for reference discovery; only matching asset IDs leave SQL.
    names = sorted(table.name for table in Base.metadata.tables.values() if table.name != Asset.__tablename__)
    quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in names)
    await db.execute(text("LOCK TABLE " + quoted + " IN SHARE ROW EXCLUSIVE MODE NOWAIT"))


async def referenced_ids(db, candidates):
    if not candidates:
        return set()
    identities = [row.id for row in candidates]
    found = set()
    for table in sorted(Base.metadata.tables.values(), key=lambda t:t.name):
        # Audit receipts describe past cleanup, not live consumers of the image.
        if table.name in {Asset.__tablename__, AdminAction.__tablename__}:
            continue
        columns = [c for c in table.columns if isinstance(c.type, (String, Text, JSON))]
        if not columns:
            continue
        conditions = [or_(func.strpos(func.lower(cast(c,Text)),func.lower(Asset.id))>0,
                          func.strpos(func.lower(cast(c,Text)),func.lower(Asset.object_key))>0) for c in columns]
        query = select(Asset.id).select_from(Asset.__table__.join(table,or_(*conditions))).where(
            Asset.id.in_(identities)).distinct()
        found.update(await db.scalars(query))
    return found


async def receipt_markers(db, action):
    rows = list(await db.scalars(select(AdminAction).where(
        AdminAction.action==action,AdminAction.status=="completed")
        .order_by(AdminAction.completed_at,AdminAction.id).limit(MAX_RECEIPTS+1)))
    if len(rows)>MAX_RECEIPTS:
        raise CleanupError("retention_history_too_large")
    times = {}
    for row in rows:
        if row.completed_at is not None:
            for identity in row.result.get("asset_ids",[]):
                times[identity] = (row.completed_at,row.id)
    return times


def policy(settings, *, allow_disabled=False):
    if settings.v2_upload_retention_days == 0 and not allow_disabled:
        raise CleanupError("upload_retention_disabled")
    return {"unused_upload_days":settings.v2_upload_retention_days,
            "expired_grace_days":settings.v2_expired_upload_grace_days}


async def inspect_locked(db, root, settings, action, *, now=None, batch_id=None, after=None, limit=None):
    if action not in ACTIONS:
        raise CleanupError("invalid_retention_action")
    if after is not None:
        try:
            if str(UUID(after)) != after:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise CleanupError("invalid_retention_cursor") from None
    limit=MAX_BATCH if limit is None else limit
    if type(limit) is not int or not 1<=limit<=MAX_BATCH:
        raise CleanupError("invalid_retention_limit")
    rules = policy(settings,allow_disabled=action=="restore")
    identity = await database_identity(db)
    now = now or utc_now()
    # Pin the cutoff to a UTC day so an inspection can be reviewed and applied.
    today = now.replace(hour=0,minute=0,second=0,microsecond=0)
    cutoff = today-timedelta(days=rules["unused_upload_days"])
    query = select(Asset).where(Asset.kind=="reference",Asset.object_key.like("uploads/%"))
    if action=="expire":
        query = query.where(Asset.status=="ready",Asset.created_at<cutoff)
    else:
        query = query.where(Asset.status=="expired")
    if action=="restore":
        source = await db.get(AdminAction,batch_id)
        if source is None or source.action!="expire_uploads" or source.status!="completed":
            raise CleanupError("expiry_receipt_required")
        query = query.where(Asset.id.in_(source.result.get("asset_ids",[])))
    if after is not None:
        query=query.where(Asset.id>after)
    rows = list(await db.scalars(query.order_by(Asset.id).limit(limit+1)))
    has_more=len(rows)>limit
    rows=rows[:limit]
    references = await referenced_ids(db,rows)
    markers = await receipt_markers(db,"restore_uploads" if action=="expire" else "expire_uploads")
    blocked = await db.scalar(select(Operation.id).where(or_(
        Operation.state.not_in(TERMINAL),
        and_(Operation.dispatch_intent_at.is_not(None),or_(
            Operation.upstream_status.is_(None),Operation.upstream_status.not_in(["completed","failed","interrupted"]))),
        and_(Operation.reserved_units>0,Operation.reservation_closed.is_(False)),
    )).limit(1))
    candidates, retained = [], []
    remote_objects = {}
    total = 0
    for row in rows:
        marker=markers.get(row.id)
        if action=="expire" and marker and marker[0]>=cutoff:
            retained.append({"asset_id":row.id,"reason":"restored_recently"})
            continue
        if action=="restore" and (not marker or marker[1]!=batch_id):
            retained.append({"asset_id":row.id,"reason":"different_expiry_batch"})
            continue
        if row.id in references and action!="restore":
            retained.append({"asset_id":row.id,"reason":"referenced"})
            continue
        if action=="purge" and (not marker or now<marker[0]+timedelta(days=rules["expired_grace_days"])):
            retained.append({"asset_id":row.id,"reason":"grace_period_or_unknown_expiry"})
            continue
        if action == "purge" and isinstance(root, RemoteAssets):
            expiry = await db.get(AdminAction, marker[1])
            manifest = expiry.result.get("manifest") if expiry else None
            if (not isinstance(manifest, dict)
                    or manifest.get("digest") != digest({k:v for k,v in manifest.items() if k != "digest"})
                    or manifest.get("storage_root") != str(root) or manifest.get("database") != identity):
                retained.append({"asset_id":row.id,"reason":"expiry_scope_unbound"})
                continue
            grace = max(rules["expired_grace_days"], manifest["policy"]["expired_grace_days"])
            if now < marker[0] + timedelta(days=grace):
                retained.append({"asset_id":row.id,"reason":"original_grace_period"})
                continue
            bound = next((item for item in manifest.get("candidates",[]) if item.get("asset_id")==row.id), None)
            if not bound or any(bound.get(k)!=v for k,v in
                    {"key":row.object_key,"sha256":row.sha256,"bytes":row.byte_size,"owner_id":row.owner_id}.items()):
                raise CleanupError("expiry_asset_changed")
            if await db.scalar(text("SELECT xmin::text::bigint = txid_current() % 4294967296 FROM v2_admin_actions WHERE id=:id"),
                               {"id":marker[1]}):
                raise CleanupError("expiry_commit_required")
        if not OBJECT.fullmatch(row.object_key) or row.object_key not in {f"uploads/{row.id}.png", f"uploads/{row.id}.jpg"}:
            raise CleanupError("unexpected_upload_key")
        if isinstance(root, RemoteAssets):
            observed = await storage_io(root.inspect, row.object_key, row.sha256, row.byte_size, missing=action=="purge")
            exists = observed["present"]
            remote_objects[row.id] = observed
            total += row.byte_size if exists else 0
            if total > MAX_BATCH_BYTES:
                raise CleanupError("retention_batch_too_large")
        else:
            path = checked(root,row.object_key)
            exists = path.exists()
            if not exists and action!="purge":
                raise CleanupError("retained_upload_missing")
            if exists:
                if path.stat().st_nlink != 1:
                    raise CleanupError("linked_upload")
                actual = fingerprint(path)
                if actual!={"sha256":row.sha256,"bytes":row.byte_size}:
                    raise CleanupError("upload_content_changed")
                total+=row.byte_size
                if total>MAX_BATCH_BYTES:
                    raise CleanupError("retention_batch_too_large")
        candidates.append({"asset_id":row.id,"key":row.object_key,"sha256":row.sha256,
                           "bytes":row.byte_size,"present":exists,"status":row.status,"lifecycle_receipt":marker[1] if marker else None})
        if isinstance(root, RemoteAssets):
            candidates[-1]["owner_id"] = row.owner_id
    report = {"format":1,"scope":"v2_unused_uploads","database":identity,
              "storage_root":str(root),"action":action,"batch_id":batch_id,"policy":rules,
              "cutoff":cutoff.isoformat(),"limit":limit,"after":after,"next_after":rows[-1].id if has_more else None,"blocked":"operation_unconfirmed" if blocked else None,
              "candidates":candidates,"retained":retained}
    if isinstance(root, RemoteAssets):
        report["remote_objects"] = remote_objects
    report["digest"]=digest(report)
    return report


async def inspect_uploads(db, storage_root, settings, action="expire", *, now=None, batch_id=None, after=None, limit=None, writers_stopped=False):
    root = RemoteAssets(settings) if settings.v2_asset_backend == "minio" else root_path(storage_root)
    policy(settings,allow_disabled=action=="restore")
    await lock_registry(db)
    if isinstance(root, RemoteAssets) or settings.app_env == "production":
        await paused(db, settings, writers_stopped)
    return await inspect_locked(db,root,settings,action,now=now,batch_id=batch_id,after=after,limit=limit)


async def apply_uploads(db, storage_root, settings, action, expected_digest, actor_id, reason,
                        *, now=None, batch_id=None, after=None, limit=None, writers_stopped=False):
    root = RemoteAssets(settings) if settings.v2_asset_backend == "minio" else root_path(storage_root)
    policy(settings,allow_disabled=action=="restore")
    if not isinstance(reason,str) or not 3<=len(reason.strip())<=500:
        raise CleanupError("maintenance_reason_required")
    if not isinstance(expected_digest,str) or len(expected_digest)!=64:
        raise CleanupError("reviewed_digest_required")
    await lock_registry(db)
    if isinstance(root, RemoteAssets) or settings.app_env == "production":
        await paused(db, settings, writers_stopped)
    actor=await db.get(User,actor_id)
    if actor is None or actor.is_banned or actor.role!=UserRole.admin:
        raise CleanupError("admin_required")
    request=digest({"action":action,"digest":expected_digest,"batch_id":batch_id,"reason":reason.strip()})
    old=await db.scalar(select(AdminAction).where(AdminAction.actor_id==actor_id,
                                                  AdminAction.client_request_id==request))
    if old:
        return {"receipt_id":old.id,**old.result}
    now=now or utc_now()
    report=await inspect_locked(db,root,settings,action,now=now,batch_id=batch_id,after=after,limit=limit)
    if report["digest"]!=expected_digest:
        raise CleanupError("inventory_changed")
    if report["blocked"]:
        raise CleanupError(report["blocked"])
    # First expiry commits a tombstone; only a later transaction may remove bytes.
    # If purge is interrupted or its DB commit fails, the row stays expired and
    # cannot be served. A new inspection can finish missing-file purges safely.
    for item in report["candidates"]:
        row=await db.get(Asset,item["asset_id"])
        if action=="purge":
            if isinstance(root, RemoteAssets):
                await storage_io(root.delete, item["key"], item["sha256"], item["bytes"])
            else:
                path=checked(root,item["key"])
                if path.exists():
                    if fingerprint(path)!={"sha256":item["sha256"],"bytes":item["bytes"]}:
                        raise CleanupError("upload_content_changed")
                    path.unlink()
            row.status="purged"
        else:
            row.status="expired" if action=="expire" else "ready"
    result={"status":"completed","action":action,"asset_ids":[r["asset_id"] for r in report["candidates"]],
            "count":len(report["candidates"]),"manifest_digest":expected_digest,"policy":report["policy"],"manifest":report}
    receipt=AdminAction(id=str(uuid4()),actor_id=actor_id,client_request_id=request,request_digest=request,
        action=action+"_uploads",target_id="global",reason=reason.strip(),status="completed",
        result=result,created_at=now,started_at=now,completed_at=now)
    db.add(receipt)
    await db.flush()
    return {"receipt_id":receipt.id,**result}


async def run(settings, args):
    if not deployment_allowed(settings):
        raise CleanupError("maintenance_configuration_required")
    engine=create_async_engine(settings.database_url,hide_parameters=True)
    try:
        await require_v2_schema(settings=settings,engine=engine)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        async with factory.begin() as db:
            if args.command=="inspect":
                return await inspect_uploads(db,Path(settings.v2_storage_dir),settings,args.action,batch_id=args.batch_id,after=args.after,limit=args.limit,writers_stopped=args.writers_stopped)
            return await apply_uploads(db,Path(settings.v2_storage_dir),settings,args.action,args.expected_digest,
                                       args.actor_id,args.reason,batch_id=args.batch_id,after=args.after,limit=args.limit,writers_stopped=args.writers_stopped)
    finally:
        await engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["inspect","apply"])
    parser.add_argument("action",choices=sorted(ACTIONS))
    parser.add_argument("--env-file",type=Path,required=True)
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--batch-id")
    parser.add_argument("--limit",type=int,default=MAX_BATCH,help="1-100 assets per inspection page")
    parser.add_argument("--after",help="next_after from the preceding inspection page")
    parser.add_argument("--expected-digest")
    parser.add_argument("--actor-id")
    parser.add_argument("--reason")
    args=parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise CleanupError("configuration_file_missing")
        result=asyncio.run(run(Settings(_env_file=args.env_file),args))
    except Exception as exc:
        print(json.dumps({"status":"refused","code":str(exc) if isinstance(exc,CleanupError) else "maintenance_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result,ensure_ascii=True))


if __name__=="__main__":
    main()
