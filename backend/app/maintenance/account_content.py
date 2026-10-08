"""Remove private legacy content of closed accounts, preserving resource tombstones."""
import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import JSON, String, Text, cast, column, func, literal, or_, select, text, update, values
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.time import utc_now
from app.db.base import Base
from app.db.models import AlbumItem, GenerationEvent, GenerationJob, GenerationOutput, Project, ProjectDraft, ProjectReference, ReferenceAsset
from app.db.v2_schema import require_v2_schema
from app.design.models import AccountClosure, AccountContentRetirement, AdminAction, Asset, Branch, Design, EditorDraft, LegacyMaterial, Operation
from app.maintenance import account_closure as closure, design_content
from app.maintenance.orphan_assets import CleanupError, database_identity, digest
from app.maintenance.runtime_retention import administrator, identity

ACTION = "scrub_account_content"
SCOPE = "v2_closed_account_content"
MAX_ROWS = 100000
MAX_BYTES = 64 * 1024 * 1024
CONTENT = {
    Project: {"name": ""},
    ProjectDraft: {"data": {}},
    ReferenceAsset: {"file_name": ""},
    GenerationJob: {"prompt_payload": {}, "phase_label": "", "error_message": None},
    GenerationEvent: {"message": "", "payload": {}},
    GenerationOutput: {"landmarks": None},
    AlbumItem: {"recipe": None, "metadata_json": None},
    LegacyMaterial: {"source_summary": {"record": {}, "project": None, "issues": ["account_content_retired"]}},
}


def scopes(owner=None, *, owners=None):
    """Relational account ownership, never an ID found inside user-supplied JSON."""
    match = (lambda column: column.in_(owners)) if owners is not None else (lambda column: column == owner)
    projects = select(Project.id).where(match(Project.user_id))
    jobs = select(GenerationJob.id).where(match(GenerationJob.user_id))
    designs = select(Design.id).where(match(Design.owner_id))
    branches = select(Branch.id).where(Branch.design_id.in_(designs))
    operations = select(Operation.id).where(match(Operation.owner_id))
    drafts = select(EditorDraft.id).where(match(EditorDraft.owner_id))
    result = {}
    for table in Base.metadata.tables.values():
        c = table.c
        if "owner_id" in c: result[table.name] = match(c.owner_id)
        elif "user_id" in c: result[table.name] = match(c.user_id)
        elif "design_id" in c: result[table.name] = c.design_id.in_(designs)
        elif "branch_id" in c: result[table.name] = c.branch_id.in_(branches)
        elif "operation_id" in c: result[table.name] = c.operation_id.in_(operations)
        elif "source_operation_id" in c: result[table.name] = c.source_operation_id.in_(operations)
        elif "draft_id" in c: result[table.name] = c.draft_id.in_(drafts)
        elif "project_id" in c: result[table.name] = c.project_id.in_(projects)
        elif "job_id" in c: result[table.name] = c.job_id.in_(jobs)
    return result


def retired_scopes(expected_owner=None):
    owners = select(AccountContentRetirement.owner_id).join(AccountClosure,
        AccountClosure.owner_id == AccountContentRetirement.owner_id).where(AccountClosure.state == "closed")
    if expected_owner is not None:
        owners = owners.where(AccountContentRetirement.owner_id == expected_owner).correlate(Asset)
    return scopes(owners=owners)


async def guard(db,settings,owner,stopped):
    if not settings.v2_account_content_cleanup_enabled: raise CleanupError("account_content_cleanup_disabled")
    await closure.guard(db,settings,stopped)
    user = await closure.target(db,owner)
    row = await db.get(AccountClosure,owner)
    if row is None or row.state != "closed" or not user.is_banned:
        raise CleanupError("closed_account_required")
    if await db.scalar(select(Design.id).where(Design.owner_id == owner, Design.content_purged_at.is_(None)).limit(1)):
        raise CleanupError("design_content_retirement_required")
    conflict = await db.scalar(select(GenerationJob.id).join(Project, Project.id == GenerationJob.project_id).where(
        GenerationJob.user_id != Project.user_id, or_(GenerationJob.user_id == owner, Project.user_id == owner)).limit(1))
    if conflict: raise CleanupError("legacy_owner_conflict")
    return row


async def inventory(db,owner):
    conditions = scopes(owner)
    records = {}
    count = size = 0
    for model in CONTENT:
        table = model.__table__
        columns = [col for col in table.c if isinstance(col.type,(String,Text,JSON))]
        total = sum((func.coalesce(func.octet_length(cast(col,Text)),0) for col in columns),literal(0))
        size += await db.scalar(select(func.coalesce(func.sum(total),0)).where(conditions[table.name])) or 0
        if size > MAX_BYTES: raise CleanupError("account_content_too_large")
        rows = (await db.execute(select(table).where(conditions[table.name]).order_by(*table.primary_key.columns)
            .limit(MAX_ROWS+1))).mappings().all()
        count += len(rows)
        if count > MAX_ROWS: raise CleanupError("account_content_too_large")
        records[table.name] = [dict(row) for row in rows]
    encoded = design_content.serialize(records)
    if len(encoded.encode()) > MAX_BYTES: raise CleanupError("account_content_too_large")
    return records,encoded,conditions


async def shared_content(db,records,conditions):
    # Financial/audit references need identities, not the removed private body.
    keys = sorted({str(row["id"]).lower() for rows in records.values() for row in rows if "id" in row})
    for start in range(0,len(keys),500):
        wanted=values(column("id",String),name="account_content_ids").data([(key,) for key in keys[start:start+500]])
        for table in Base.metadata.tables.values():
            if table.name in {AdminAction.__tablename__,"v2_ledger"}:continue
            columns=[c for c in table.c if isinstance(c.type,(String,Text,JSON))]
            if not columns:continue
            condition=or_(*(func.strpos(func.lower(cast(c,Text)),wanted.c.id)>0 for c in columns))
            query=select(literal(True)).select_from(wanted.join(table,condition))
            if table.name in conditions:query=query.where(~func.coalesce(conditions[table.name],False))
            if await db.scalar(query.limit(1)):return True
    return False


async def inspect_locked(db,settings,owner):
    marker=await db.get(AccountContentRetirement,owner)
    if marker:
        report={"format":1,"scope":SCOPE,"database":await database_identity(db),"owner_id":owner,
            "status":"scrubbed","source_digest":marker.source_digest,"resources":marker.resources}
    else:
        records,encoded,conditions=await inventory(db,owner)
        if await shared_content(db,records,conditions):raise CleanupError("shared_account_content")
        resources=await design_content.resource_references(db,records,encoded)
        from app.maintenance.container_backup import owned_threads
        known=await owned_threads(db,include_retired=True)
        unknown=[]
        for item in resources["native_threads"]:
            key=item["thread_id"]
            if key in known and known[key] != owner:raise CleanupError("shared_native_content")
            if key not in known:unknown.append(key)
        # Unknown original IDs remain explicitly preserved without asserting that
        # an unregistered personal thread belongs to this account.
        resources["native_threads"]=[item for item in resources["native_threads"] if item["thread_id"] not in unknown]
        resources["unmapped_native_ids"]=unknown
        report={"format":1,"scope":SCOPE,"database":await database_identity(db),"owner_id":owner,
            "status":"pending","source_digest":digest(json.loads(encoded)),
            "counts":{key:len(rows) for key,rows in records.items()},"resources":resources}
    report["digest"]=digest(report)
    return report


async def inspect_account(db,settings,owner,*,writers_stopped=False):
    await guard(db,settings,owner,writers_stopped)
    return await inspect_locked(db,settings,owner)


async def scrub_account(db,settings,owner,expected_digest,actor,reason,*,writers_stopped=False,now=None):
    await guard(db,settings,owner,writers_stopped)
    await administrator(db,actor,reason)
    request=digest({"action":ACTION,"owner":owner,"digest":expected_digest,"reason":reason.strip()})
    replay=await closure.prior(db,actor,request)
    if replay:return replay
    report=await inspect_locked(db,settings,owner)
    if report["digest"] != expected_digest:raise CleanupError("account_content_changed")
    if report["status"] != "pending":raise CleanupError("account_content_already_retired")
    now=now or utc_now()
    async with db.begin_nested():
        enabled=await db.scalar(text("SELECT tgenabled::text FROM pg_trigger "
            "WHERE tgrelid='v2_legacy_materials'::regclass AND tgname='v2_legacy_material_guard'"))
        if enabled != "O":raise CleanupError("legacy_material_guard_unavailable")
        await db.execute(text("ALTER TABLE v2_legacy_materials DISABLE TRIGGER v2_legacy_material_guard"))
        conditions=scopes(owner)
        for model,fields in CONTENT.items():
            await db.execute(update(model).where(conditions[model.__tablename__]).values(**fields)
                .execution_options(synchronize_session=False))
        await db.execute(text("ALTER TABLE v2_legacy_materials ENABLE TRIGGER v2_legacy_material_guard"))
        db.add(AccountContentRetirement(owner_id=owner,source_digest=report["source_digest"],
            resources=report["resources"],created_at=now))
        await db.flush()
        return await closure.receipt(db,actor,reason,ACTION,request,{"owner_id":owner,
            "status":"content_scrubbed","files_erased":False,"backups_erased":False,"manifest":report},now)


async def run(settings,args):
    engine=create_async_engine(settings.database_url,hide_parameters=True)
    try:
        await require_v2_schema(settings=settings,engine=engine)
        async with async_sessionmaker(engine,expire_on_commit=False).begin() as db:
            options={"writers_stopped":args.writers_stopped}
            if args.command=="inspect":return await inspect_account(db,settings,args.owner_id,**options)
            return await scrub_account(db,settings,args.owner_id,args.expected_digest,args.actor_id,args.reason,**options)
    finally:await engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["inspect","apply"])
    parser.add_argument("--env-file",type=Path,required=True)
    parser.add_argument("--owner-id",required=True)
    parser.add_argument("--writers-stopped",action="store_true")
    for name in ("expected-digest","actor-id","reason"):parser.add_argument("--"+name)
    args=parser.parse_args()
    try:
        if not args.env_file.is_file():raise CleanupError("configuration_file_missing")
        result=asyncio.run(run(Settings(_env_file=args.env_file),args))
    except Exception as exc:
        print(json.dumps({"status":"refused","code":str(exc) if isinstance(exc,CleanupError) else "maintenance_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result,ensure_ascii=True))


if __name__=="__main__":main()
