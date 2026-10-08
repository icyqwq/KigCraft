"""Retire original legacy images and closed-account operation files, never profiles."""
import argparse
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
import json
import os
import re
from pathlib import Path

from sqlalchemy import JSON, String, Text, cast, column, func, or_, select, text, values
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assets.repository import storage_io
from app.codex_runtime.container_runtime import ContainerRuntime
from app.codex_runtime.profile_lock import ProfileLease
from app.core.config import Settings
from app.core.time import utc_now
from app.db.base import Base
from app.db.models import AlbumItem, GenerationJob, GenerationOutput, Project, ProjectReference, ReferenceAsset
from app.db.v2_schema import require_v2_schema
from app.design.models import AccountClosure, AccountContentRetirement, AdminAction, Operation
from app.maintenance import account_content as content, account_closure as closure, runtime_retention as runtime
from app.maintenance.container_backup import account_inventory
from app.maintenance.legacy_materials import reference_key
from app.maintenance.orphan_assets import CleanupError, MAX_BATCH_BYTES, MAX_FILES, checked, database_identity, digest, root_path
from app.maintenance.runtime_references import native_references, tokens
from app.maintenance.v2_migration import generated_key

SCOPE="v2_closed_account_files"
RETIRE="retire_account_files"
PURGE="purge_account_files"
MAX_BATCH=100
GROUPS={"legacy_generated","legacy_references","work","inputs"}


def item_id(item): return item["group"]+"/"+item["key"]


async def business_references(db,items):
    if not items:return set()
    lookup=tokens([{"key":item_id(item)} for item in items])
    pairs=[(key,token) for token,keys in lookup.items() for key in keys]
    wanted=values(column("key",String),column("token",String),name="account_file_refs").data(pairs)
    retired=content.retired_scopes()
    protected=set()
    for table in Base.metadata.tables.values():
        if table.name==AdminAction.__tablename__:continue
        cols=[col for col in table.c if isinstance(col.type,(String,Text,JSON))]
        if not cols:continue
        match=or_(*(func.strpos(func.lower(cast(col,Text)),wanted.c.token)>0 for col in cols))
        query=select(wanted.c.key).select_from(wanted.join(table,match)).distinct()
        if table.name in retired:query=query.where(~func.coalesce(retired[table.name],False))
        protected.update(await db.scalars(query))
    return protected


@asynccontextmanager
async def environment(db,settings,owner,roots,stopped,profile):
    if not settings.v2_account_file_cleanup_enabled:raise CleanupError("account_file_cleanup_disabled")
    await content.guard(db,settings,owner,stopped)
    marker=await db.get(AccountContentRetirement,owner)
    if marker is None:raise CleanupError("account_content_retirement_required")
    if set(roots)!={"legacy_generated","legacy_references"}:raise CleanupError("legacy_roots_required")
    roots={key:root_path(path) for key,path in roots.items()}
    profiles={};runtimes=[]
    async with AsyncExitStack() as stack:
        if settings.v2_runtime_mode=="local":
            home=root_path(profile or Path(os.environ.get("CODEX_HOME",str(Path.home()/".codex"))))
            lease=ProfileLease(home).acquire();stack.callback(lease.release,clean=True)
            profiles["local"]=home
            roots["work"]=root_path(Path(settings.v2_runtime_dir))
        else:
            if profile is not None:raise CleanupError("production_profile_is_configuration_bound")
            owners=sorted(settings.v2_container_user_ids)
            if owner not in owners:raise CleanupError("account_runtime_not_configured")
            base=root_path(Path(settings.v2_container_root))
            account_inventory(base,owners)
            for identity in owners:
                bound=ContainerRuntime(settings.model_copy(update={"v2_worker_user_id":identity}))
                await stack.enter_async_context(bound.operation())
                await bound.verify_stopped()
                runtimes.append(bound);profiles[identity]=root_path(bound.profile)
                if identity==owner:
                    roots["work"]=root_path(bound.work_root);roots["inputs"]=root_path(bound.input_root)
        paths=[*roots.values(),*profiles.values(),Path(settings.v2_storage_dir).resolve()]
        for i,left in enumerate(paths):
            for right in paths[i+1:]:
                if left.is_relative_to(right) or right.is_relative_to(left):raise CleanupError("account_file_root_overlap")
        yield roots,profiles,runtimes,marker
        for bound in runtimes:await bound.verify_stopped()


async def locations(db,owner,roots):
    """Only registered legacy locators and directories of owned terminal operations."""
    result=set()
    def add(group,key):
        if not key:raise CleanupError("unmapped_account_file")
        checked(roots[group],key)
        result.add((group,key))
        if len(result)>MAX_FILES:raise CleanupError("account_file_inventory_too_large")
    async def bounded(query):
        rows=list(await db.scalars(query.limit(MAX_FILES+1)))
        if len(rows)>MAX_FILES:raise CleanupError("account_file_inventory_too_large")
        return rows
    outputs=await bounded(select(GenerationOutput).join(GenerationJob,
        GenerationJob.id==GenerationOutput.job_id).where(GenerationJob.user_id==owner))
    for row in outputs:
        if row.image_url and generated_key(row.image_url,"/api/generated")!=row.object_key:raise CleanupError("legacy_locator_conflict")
        add("legacy_generated",row.object_key)
    for url in await bounded(select(AlbumItem.image_url).where(AlbumItem.user_id==owner)):
        if url:add("legacy_generated",generated_key(url,"/api/generated"))
    for key in await bounded(select(ReferenceAsset.object_key).where(ReferenceAsset.user_id==owner)):
        add("legacy_references",reference_key(key))
    for key in await bounded(select(ProjectReference.object_key).join(Project).where(Project.user_id==owner)):
        add("legacy_references",reference_key(key))
    operations=list(await db.scalars(select(Operation).where(Operation.owner_id==owner).limit(MAX_FILES+1)))
    if len(operations)>MAX_FILES:raise CleanupError("account_file_inventory_too_large")
    entries=0
    for operation in operations:
        if not runtime.identity(operation.id) or not runtime.closed(operation):raise CleanupError("operation_unconfirmed")
        for group in ("work","inputs"):
            if group not in roots:continue
            folder=checked(roots[group],operation.id)
            if not folder.exists():continue
            if not folder.is_dir():raise CleanupError("unsafe_operation_directory")
            folders=[folder]
            while folders:
                for path in folders.pop().iterdir():
                    entries+=1
                    if entries>MAX_FILES:raise CleanupError("account_file_inventory_too_large")
                    key=path.relative_to(roots[group]).as_posix();path=checked(roots[group],key)
                    if path.is_dir():folders.append(path)
                    else:add(group,key)
    return sorted(result)


async def native_protection(settings,profiles,items):
    protected=set();proof={}
    candidates=[{"key":item_id(item)} for item in items]
    for owner,profile in sorted(profiles.items()):
        found,value=await storage_io(native_references,profile,candidates,settings.v2_codex_version)
        protected.update(found);proof[owner]=value
    return protected,proof


def manifest(row):
    if row is None or row.action!=RETIRE or row.status!="completed":raise CleanupError("account_file_retirement_required")
    value=(row.result or {}).get("manifest")
    if (not isinstance(value,dict) or value.get("format")!=1 or value.get("scope")!=SCOPE
        or value.get("digest")!=digest({key:item for key,item in value.items() if key!="digest"})
        or not runtime.identity(value.get("owner_id")) or type(value.get("grace_days")) is not int
        or not 1<=value["grace_days"]<=3650):
        raise CleanupError("invalid_account_file_manifest")
    items=value.get("candidates")
    if not isinstance(items,list) or len(items)>MAX_BATCH:raise CleanupError("invalid_account_file_manifest")
    seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)!={"group","key","sha256","bytes","mtime_ns"} or item["group"] not in GROUPS:
            raise CleanupError("invalid_account_file_manifest")
        if (not isinstance(item["sha256"],str) or not re.fullmatch(r"[0-9a-f]{64}",item["sha256"])
            or type(item["bytes"]) is not int or not 0<=item["bytes"]<=MAX_BATCH_BYTES
            or type(item["mtime_ns"]) is not int or item["mtime_ns"]<0):
            raise CleanupError("invalid_account_file_manifest")
        key=item["key"]
        if not isinstance(key,str) or not key or "\\" in key or ":" in key or any(part in {"",".",".."} for part in key.split("/")):
            raise CleanupError("invalid_account_file_manifest")
        if item["group"] in {"work","inputs"} and not runtime.identity(key.split("/")[0]):
            raise CleanupError("invalid_account_file_manifest")
        if item_id(item) in seen:raise CleanupError("invalid_account_file_manifest")
        seen.add(item_id(item))
    return value


async def completed_files(db):
    """Logical tombstones survive backup relocation; never omit new live references."""
    rows=list(await db.scalars(select(AdminAction).where(AdminAction.action==PURGE,AdminAction.status=="completed").limit(10001)))
    if len(rows)>10000:raise CleanupError("account_file_receipts_too_large")
    result={}
    for row in rows:
        value=manifest(await db.get(AdminAction,row.result.get("batch_id")))
        marker=await db.get(AccountContentRetirement,value["owner_id"])
        closed=await db.get(AccountClosure,value["owner_id"])
        if (marker is None or closed is None or closed.state!="closed" or marker.source_digest!=value["content_digest"]
            or row.result.get("manifest_digest")!=value["digest"]):raise CleanupError("invalid_account_file_completion")
        if await business_references(db,value["candidates"]):raise CleanupError("retired_account_file_referenced")
        for item in value["candidates"]:
            key=(item["group"],item["key"])
            if key in result and result[key]["sha256"]!=item["sha256"]:raise CleanupError("conflicting_account_file_retirement")
            result[key]={**item,"owner_id":value["owner_id"],"receipt_id":row.id}
    return result


async def inspect_locked(db,settings,owner,roots,profiles,marker,after,limit):
    if type(limit)is not int or not 1<=limit<=MAX_BATCH or (after is not None and (not isinstance(after,str) or len(after)>2048)):
        raise CleanupError("invalid_retention_page")
    located=await locations(db,owner,roots)
    located=[pair for pair in located if after is None or "/".join(pair)>after]
    selected=located[:limit]
    done=await completed_files(db)
    candidates=[];retained=[];size=0
    for group,key in selected:
        path=checked(roots[group],key)
        if not path.exists():
            if (group,key) not in done:raise CleanupError("unconfirmed_account_file_missing")
            continue
        info=runtime.regular(roots[group],key);size+=info["bytes"]
        if size>MAX_BATCH_BYTES:raise CleanupError("account_file_batch_too_large")
        candidates.append({"group":group,"key":key,"mtime_ns":path.stat().st_mtime_ns,**info})
    protected,proof=await native_protection(settings,profiles,candidates)
    protected.update(await business_references(db,candidates))
    for item in candidates:
        if item_id(item) in protected:retained.append({"group":item["group"],"key":item["key"],"reason":"shared_or_native_reference"})
    report={"format":1,"scope":SCOPE,"owner_id":owner,"database":await database_identity(db),
        "roots":{key:str(path) for key,path in roots.items()},"profiles":{key:str(path) for key,path in profiles.items()},
        "runtime_mode":settings.v2_runtime_mode,"codex_version":settings.v2_codex_version,
        "native_proof":proof,"content_digest":marker.source_digest,"grace_days":settings.v2_account_file_grace_days,
        "after":after,"limit":limit,"next_after":"/".join(selected[-1]) if len(located)>limit else None,
        "candidates":[item for item in candidates if item_id(item) not in protected],"retained":retained}
    report["digest"]=digest(report)
    return report


async def inspect_files(db,settings,owner,roots,*,writers_stopped=False,profile=None,after=None,limit=MAX_BATCH):
    async with environment(db,settings,owner,roots,writers_stopped,profile) as (bound,profiles,_,marker):
        return await inspect_locked(db,settings,owner,bound,profiles,marker,after,limit)


async def retire_files(db,settings,owner,roots,expected_digest,actor,reason,*,writers_stopped=False,profile=None,after=None,limit=MAX_BATCH,now=None):
    async with environment(db,settings,owner,roots,writers_stopped,profile) as (bound,profiles,_,marker):
        await runtime.administrator(db,actor,reason)
        request=digest({"action":RETIRE,"digest":expected_digest,"owner":owner,"reason":reason.strip()})
        replay=await closure.prior(db,actor,request)
        if replay:return replay
        report=await inspect_locked(db,settings,owner,bound,profiles,marker,after,limit)
        if report["digest"]!=expected_digest:raise CleanupError("account_file_inventory_changed")
        return await closure.receipt(db,actor,reason,RETIRE,request,{"owner_id":owner,"status":"retired","manifest":report,
            "transaction":{"database":await database_identity(db),"id":await db.scalar(text("SELECT txid_current()"))}},now or utc_now())


async def purge_files(db,settings,owner,roots,batch_id,actor,reason,*,writers_stopped=False,profile=None,now=None):
    async with environment(db,settings,owner,roots,writers_stopped,profile) as (bound,profiles,runtimes,marker):
        await runtime.administrator(db,actor,reason)
        row=await db.get(AdminAction,batch_id);value=manifest(row)
        expected={"owner_id":owner,"database":await database_identity(db),"roots":{k:str(v) for k,v in bound.items()},
            "profiles":{k:str(v) for k,v in profiles.items()},"runtime_mode":settings.v2_runtime_mode,
            "codex_version":settings.v2_codex_version,"content_digest":marker.source_digest}
        if any(value.get(k)!=v for k,v in expected.items()):raise CleanupError("account_file_scope_mismatch")
        if row.result.get("transaction")=={"database":expected["database"],"id":await db.scalar(text("SELECT txid_current()"))}:
            raise CleanupError("retirement_commit_required")
        now=now or utc_now()
        if row.completed_at is None or now<row.completed_at+timedelta(days=max(value["grace_days"],settings.v2_account_file_grace_days)):
            raise CleanupError("retirement_grace_period")
        request=digest({"action":PURGE,"batch":batch_id,"reason":reason.strip()})
        replay=await closure.prior(db,actor,request)
        if replay:return replay
        locations_now=set(await locations(db,owner,bound))
        # Runtime files may be absent after an interrupted deletion; ownership is
        # still proven by their operation row, never inferred from a directory.
        for item in value["candidates"]:
            if item["group"] in {"work","inputs"}:
                op=await db.get(Operation,item["key"].split("/")[0])
                if op is None or op.owner_id!=owner or not runtime.closed(op):raise CleanupError("account_file_owner_changed")
            elif (item["group"],item["key"]) not in locations_now:raise CleanupError("legacy_locator_changed")
        protected,_=await native_protection(settings,profiles,value["candidates"])
        if protected or await business_references(db,value["candidates"]):raise CleanupError("account_file_reference_added")
        for runtime_instance in runtimes:await runtime_instance.verify_stopped()
        for item in value["candidates"]:
            path=checked(bound[item["group"]],item["key"])
            if path.exists() and (path.stat().st_mtime_ns!=item["mtime_ns"] or runtime.regular(bound[item["group"]],item["key"])!={k:item[k] for k in ("sha256","bytes")}):
                raise CleanupError("account_file_changed")
        for item in value["candidates"]:
            path=checked(bound[item["group"]],item["key"])
            if path.exists():
                if path.stat().st_mtime_ns!=item["mtime_ns"] or runtime.regular(bound[item["group"]],item["key"])!={k:item[k] for k in ("sha256","bytes")}:
                    raise CleanupError("account_file_changed")
                path.unlink()
        return await closure.receipt(db,actor,reason,PURGE,request,{"owner_id":owner,"status":"purged",
            "batch_id":batch_id,"manifest_digest":value["digest"],"count":len(value["candidates"])},now)


async def run(settings,args):
    engine=create_async_engine(settings.database_url,hide_parameters=True)
    try:
        await require_v2_schema(settings=settings,engine=engine)
        async with async_sessionmaker(engine,expire_on_commit=False).begin() as db:
            roots={"legacy_generated":args.legacy_generated,"legacy_references":args.legacy_references}
            options={"writers_stopped":args.writers_stopped}
            if args.command=="inspect":return await inspect_files(db,settings,args.owner_id,roots,after=args.after,limit=args.limit,**options)
            if args.command=="retire":return await retire_files(db,settings,args.owner_id,roots,args.expected_digest,args.actor_id,args.reason,after=args.after,limit=args.limit,**options)
            return await purge_files(db,settings,args.owner_id,roots,args.batch_id,args.actor_id,args.reason,**options)
    finally:await engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["inspect","retire","purge"])
    for name in ("env-file","legacy-generated","legacy-references"):parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--owner-id",required=True);parser.add_argument("--writers-stopped",action="store_true")
    parser.add_argument("--limit",type=int,default=MAX_BATCH)
    for name in ("after","expected-digest","actor-id","reason","batch-id"):parser.add_argument("--"+name)
    args=parser.parse_args()
    try:
        if not args.env_file.is_file():raise CleanupError("configuration_file_missing")
        result=asyncio.run(run(Settings(_env_file=args.env_file),args))
    except Exception as exc:
        print(json.dumps({"status":"refused","code":str(exc) if isinstance(exc,CleanupError) else "maintenance_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result,ensure_ascii=True))


if __name__=="__main__":main()
