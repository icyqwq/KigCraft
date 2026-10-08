"""Private, quiescent local or per-account container backup bundles. Never starts a worker or model.

The database dump includes authentication data. Store the bundle on protected
storage; the manifest is an integrity check, not encryption or a signature.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

from sqlalchemy import JSON, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.assets.repository import storage_io
from app.maintenance import backup_assets
from app.maintenance.backup_assets import RemoteAsset
from app.codex_runtime.launch import DESIGN_MODEL
from app.core.config import Settings
from app.core.time import utc_now
from app.db.base import Base
from app.db.models import AlbumItem, GenerationJob, GenerationOutput, ProjectReference, ReferenceAsset
from app.db.v2_schema import V2_SCHEMA_VERSION, require_v2_schema
from app.design.models import AdminAction, Asset, CodexEpoch, Operation, RuntimeControl
from app.maintenance.codex_snapshot import (
    SnapshotError, checked_path, create_snapshot, digest as file_digest, verify_snapshot,
)
from app.maintenance.orphan_assets import CleanupError, checked, database_identity, maintenance_lock, root_path, write_new
from app.maintenance.legacy_materials import reference_key
from app.maintenance.v2_migration import generated_key, valid_key

MAX_ROWS = 100000
MAX_FILES = 100000
MAX_FILE_BYTES = 4 * 1024**3
MAX_TOTAL_BYTES = 64 * 1024**3
MAX_MANIFEST_BYTES = 32 * 1024**2
SCOPE = "v2_local_complete_backup"
CONTAINER_SCOPE = "v2_container_complete_backup"


class BackupError(ValueError):
    pass


def canonical_root(path):
    # Inspect lexical parents before resolving; resolving first hides junctions.
    return root_path(Path(path))


def new_destination(path, roots):
    path=Path(path)
    if not path.is_absolute() or path.exists():
        raise BackupError("new_absolute_backup_directory_required")
    for part in (path,*path.parents):
        if part.is_symlink() or part.is_junction():
            raise BackupError("linked_backup_directory")
    resolved=path.resolve()
    for root in roots:
        if resolved.is_relative_to(root) or root.is_relative_to(resolved):
            raise BackupError("backup_source_overlap")
    return resolved


def copy_checked(source, destination):
    info=source.stat(follow_symlinks=False)
    if source.is_symlink() or source.is_junction() or not source.is_file() or info.st_size>MAX_FILE_BYTES:
        raise BackupError("unsafe_or_oversized_backup_file")
    before=file_digest(source)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src,dst,1024*1024)
        dst.flush()
        os.fsync(dst.fileno())
    after=source.stat(follow_symlinks=False)
    if ((info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
            or file_digest(source)!=before or file_digest(destination)!=before):
        raise BackupError("backup_source_changed")
    return {"bytes":info.st_size,"sha256":before}


def file_inventory(root, *, exclude_control=True):
    result={}
    total=0
    stack=[root]
    while stack:
        directory=stack.pop()
        for entry in directory.iterdir():
            if entry.is_symlink() or entry.is_junction():
                raise BackupError("linked_backup_file")
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                key=entry.relative_to(root).as_posix()
                if exclude_control and key in {"manifest.json","INCOMPLETE"}:
                    continue
                if len(result)>=MAX_FILES or entry.stat().st_size>MAX_FILE_BYTES:
                    raise BackupError("backup_too_large")
                total+=entry.stat().st_size
                if total>MAX_TOTAL_BYTES:
                    raise BackupError("backup_too_large")
                checked_path(root,key)
                result[key]={"bytes":entry.stat().st_size,"sha256":file_digest(entry)}
            else:
                raise BackupError("unsupported_backup_file")
    return result


def pg_environment(database_url):
    url=make_url(database_url)
    if url.drivername not in {"postgresql","postgresql+asyncpg"} or not url.database:
        raise BackupError("postgresql_url_required")
    if set(url.query)-{"sslmode","sslrootcert","sslcert","sslkey"}:
        raise BackupError("unsupported_postgresql_connection_options")
    env={k:os.environ[k] for k in ("PATH","SYSTEMROOT","WINDIR","TEMP","TMP","LANG") if k in os.environ}
    env.update(PGHOST=url.host or "localhost",PGPORT=str(url.port or 5432),
               PGDATABASE=url.database,PGUSER=url.username or "",PGPASSWORD=url.password or "",
               PGPASSFILE=os.devnull,PGAPPNAME="kig-v2-backup",PGTZ="UTC")
    env.update({("PG"+k.upper()):str(v) for k,v in url.query.items()})
    return env


def dump_database(executable, database_url, snapshot, destination, server_major):
    env=pg_environment(database_url)
    flags={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
    probe=subprocess.run([str(executable),"--version"],env=env,stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL,timeout=15,check=False,**flags)
    match=re.fullmatch(rb"pg_dump \(PostgreSQL\) (\d+)(?:\.[^\r\n]+)?\s*",probe.stdout)
    if probe.returncode or not match or int(match[1])!=server_major:
        raise BackupError("pg_dump_version_mismatch")
    if not re.fullmatch(r"[0-9A-Fa-f-]+",snapshot):
        raise BackupError("invalid_database_snapshot")
    with destination.open("xb") as output:
        result=subprocess.run([str(executable),"-w","-Fc","--no-owner","--no-acl","--snapshot="+snapshot],
            env=env,stdout=output,stderr=subprocess.DEVNULL,timeout=3600,check=False,**flags)
        output.flush()
        os.fsync(output.fileno())
    if result.returncode:
        raise BackupError("database_dump_failed")
    with destination.open("rb") as stream:
        if stream.read(5)!=b"PGDMP":
            raise BackupError("invalid_database_dump")


async def bounded(db, query):
    rows=list(await db.scalars(query.limit(MAX_ROWS+1)))
    if len(rows)>MAX_ROWS:
        raise BackupError("backup_inventory_too_large")
    return rows


async def freeze(db):
    await maintenance_lock(db)
    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
    await db.execute(text("SET LOCAL statement_timeout = '30s'"))
    await db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(7272192201)"))
    names=list(await db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")))
    expected={table.name for table in Base.metadata.tables.values()}|{"alembic_version"}
    if set(names)!=expected or any(not re.fullmatch(r"[a-z0-9_]+",name) for name in names):
        raise BackupError("unsupported_backup_catalog")
    await db.execute(text("LOCK TABLE "+",".join('"'+name+'"' for name in names)+" IN SHARE MODE NOWAIT"))
    if await db.scalar(text("SELECT version_num FROM public.alembic_version"))!=V2_SCHEMA_VERSION:
        raise BackupError("schema_changed")
    if not await db.scalar(select(RuntimeControl.blocked).where(RuntimeControl.scope=="global")):
        raise BackupError("generation_must_be_paused")
    now=utc_now()
    if await db.scalar(select(Operation.id).where(Operation.lease_expires_at>now).limit(1)):
        raise BackupError("active_worker_lease")
    if await db.scalar(select(AdminAction.id).where(AdminAction.status.in_(["pending","running"])).limit(1)):
        raise BackupError("pending_admin_action")
    # A legacy executor cannot participate in the V2 storage coordination lock.
    for job in await bounded(db,select(GenerationJob)):
        if str(getattr(job.status,"value",job.status)) not in {"succeeded","failed","cancelled","expired"}:
            raise BackupError("legacy_execution_not_quiescent")
    return names


async def table_fingerprints(db,names):
    result={}
    for name in names:
        if not re.fullmatch(r"[a-z0-9_]+",name):
            raise BackupError("unsupported_backup_catalog")
        rows=list(await db.scalars(text('SELECT to_jsonb(t)::text FROM "'+name+'" t ORDER BY to_jsonb(t)::text LIMIT :bound'),
                                   {"bound":MAX_ROWS+1}))
        if len(rows)>MAX_ROWS:
            raise BackupError("backup_inventory_too_large")
        result[name]={"rows":len(rows),"sha256":hashlib.sha256("\n".join(rows).encode()).hexdigest()}
    return result


def thread_references(value):
    result=set()
    if isinstance(value,dict):
        for key,item in value.items():
            if key in {"thread_id","threadId","upstream_thread_id"} and item is not None:
                if not isinstance(item,str):
                    raise BackupError("invalid_native_dependency")
                result.add(item)
            elif isinstance(item,(dict,list)):
                result.update(thread_references(item))
    elif isinstance(value,list):
        for item in value:
            result.update(thread_references(item))
    return result


async def sequence_state(db):
    names=list(await db.scalars(text("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")))
    result={}
    for name in names:
        if not re.fullmatch(r"[a-z0-9_]+",name):
            raise BackupError("unsupported_backup_catalog")
        row=(await db.execute(text('SELECT last_value,is_called FROM "'+name+'"'))).one()
        result[name]={"last_value":row.last_value,"is_called":row.is_called}
    return result


async def dependencies(db, roots, *, accounts=None, asset_repository=None):
    roots = dict(roots)
    if accounts is not None:
        roots.update({f"accounts/{owner}/{kind}": path for owner, paths in accounts.items() for kind, path in paths.items()})
    files={}
    absent=[]
    from app.maintenance.account_files import completed_files
    retired_account_files=await completed_files(db)
    def add_legacy(group,key):
        if group not in roots or not valid_key(key):
            raise BackupError("unmapped_asset_dependency")
        retirement=retired_account_files.get((group,key))
        if not checked(roots[group],key).exists() and retirement:
            value={"group":group,"key":key,"state":"retired_account_file","receipt_id":retirement["receipt_id"]}
            if value not in absent:absent.append(value)
            return
        add(group,key)
    def add(group,key,expected=None):
        if group not in roots:
            raise BackupError("required_storage_root_missing")
        if not valid_key(key):
            raise BackupError("unmapped_asset_dependency")
        source=checked_path(roots[group],key)
        relative=group+"/"+key
        if relative in files and expected and files[relative][1] not in (None,expected):
            raise BackupError("conflicting_asset_metadata")
        files[relative]=(source,expected or files.get(relative,(None,None))[1])
    for asset in await bounded(db,select(Asset)):
        if asset.status=="purged":
            absent.append({"asset_id":asset.id,"state":"purged"})
            continue
        if asset.status not in {"ready","expired"}:
            raise BackupError("unknown_asset_state")
        if asset_repository is not None:
            asset_repository.remote_key(asset.object_key)
            if not 0 < asset.byte_size <= backup_assets.MAX_OBJECT_BYTES:
                raise BackupError("asset_too_large")
            files["assets/"+asset.object_key] = (
                RemoteAsset(asset_repository, asset.object_key, asset.sha256, asset.byte_size),
                {"sha256": asset.sha256, "bytes": asset.byte_size})
            continue
        if asset.status=="expired" and not checked(roots["assets"],asset.object_key).exists():
            absent.append({"asset_id":asset.id,"state":"expired_without_file"})
            continue
        add("assets",asset.object_key,{"sha256":asset.sha256,"bytes":asset.byte_size})
    for row in await bounded(db,select(GenerationOutput)):
        add_legacy("legacy_generated",row.object_key)
        if row.image_url and generated_key(row.image_url,"/api/generated")!=row.object_key:
            raise BackupError("unmapped_legacy_url")
    for url in await bounded(db,select(AlbumItem.image_url)):
        if not url:
            continue
        key=generated_key(url,"/api/generated")
        if key is None:
            raise BackupError("unmapped_legacy_url")
        add_legacy("legacy_generated",key)
    for model in (ReferenceAsset,ProjectReference):
        for key in await bounded(db,select(model.object_key)):
            add_legacy("legacy_references",reference_key(key))
    ops=await bounded(db,select(Operation))
    def runtime_group(op):
        if accounts is None:
            return "runtime"
        if op.owner_id not in accounts:
            raise BackupError("runtime_account_missing")
        return f"accounts/{op.owner_id}/work"
    groups = {"runtime": {row.id for row in ops}} if accounts is None else {
        f"accounts/{owner}/{kind}": {row.id for row in ops if row.owner_id == owner}
        for owner in accounts for kind in ("work", "inputs")}
    for group, ids in groups.items():
        if group not in roots:
            raise BackupError("required_storage_root_missing")
        for path in roots[group].iterdir():
            if path.is_symlink() or path.is_junction() or not path.is_dir() or path.name not in ids:
                raise BackupError("unregistered_runtime_directory")
            for relative in file_inventory(path,exclude_control=False):
                add(group,path.name+"/"+relative)
    # No silent omission of staging recorded in the database.
    for op in ops:
        for descriptor in (op.staged_outputs or {}).values():
            filename=descriptor.get("filename")
            if not isinstance(filename,str) or filename not in {descriptor.get("delivery_key","")+".png",descriptor.get("delivery_key","")+".jpg"} or Path(filename).name!=filename:
                raise BackupError("invalid_staging_dependency")
            group=runtime_group(op)
            key=op.id+"/staging/"+filename
            if not checked(roots[group],key).exists():
                from app.maintenance.runtime_retention import closed, retired_staging
                retirement = await retired_staging(db,op.id,descriptor) if closed(op) else False
                if not retirement:
                    account_file=retired_account_files.get(("work",key))
                    if (account_file and account_file["owner_id"]==op.owner_id
                        and account_file["sha256"]==descriptor.get("sha256")):
                        retirement=account_file["receipt_id"]
                if not retirement:
                    raise BackupError("staging_dependency_missing")
                absent.append({"operation_id":op.id,"filename":filename,
                               "state":"retired_temporary_file","receipt_id":retirement})
                continue
            add(group,key)
            if file_digest(files[group+"/"+key][0])!=descriptor.get("sha256"):
                raise BackupError("staging_hash_mismatch")
    for (kind,key), retirement in retired_account_files.items():
        if kind not in {"work","inputs"}:continue
        group=("runtime" if kind=="work" else None) if accounts is None else f"accounts/{retirement['owner_id']}/{kind}"
        if group is None:continue
        if group not in roots:raise BackupError("runtime_account_missing")
        if not checked(roots[group],key).exists():
            absent.append({"group":group,"key":key,"state":"retired_account_file","receipt_id":retirement["receipt_id"]})
    threads=set(await db.scalars(select(CodexEpoch.thread_id)))
    threads.update(row.upstream_thread_id for row in ops if row.upstream_thread_id)
    for table in Base.metadata.tables.values():
        if not table.name.startswith("v2_") or table.name==AdminAction.__tablename__:
            continue
        for column in table.columns:
            if isinstance(column.type,JSON):
                for value in await bounded(db,select(column)):
                    threads.update(thread_references(value))
    try:
        if any(str(UUID(identity))!=identity for identity in threads):
            raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise BackupError("invalid_native_dependency") from None
    if len(files)>MAX_FILES:
        raise BackupError("backup_too_large")
    from app.maintenance.native_history import retired_threads
    for identity, receipt_id in (await retired_threads(db)).items():
        if identity in threads:
            threads.remove(identity)
            absent.append({"thread_id": identity, "state": "retired_native_history", "receipt_id": receipt_id})
    return files,sorted(threads),absent


def verify_bundle(root, *, expected_manifest_sha256=None, allow_incomplete=False):
    root=canonical_root(root)
    if (root/"INCOMPLETE").exists() and not allow_incomplete:
        raise BackupError("incomplete_backup")
    path=checked_path(root,"manifest.json")
    if path.stat().st_size>MAX_MANIFEST_BYTES:
        raise BackupError("backup_manifest_too_large")
    if expected_manifest_sha256 and file_digest(path)!=expected_manifest_sha256:
        raise BackupError("backup_manifest_changed")
    manifest=json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("format")!=1 or manifest.get("scope") not in {SCOPE,CONTAINER_SCOPE} or manifest.get("schema")!=V2_SCHEMA_VERSION
            or manifest.get("model")!=DESIGN_MODEL or manifest.get("restore_policy")!=
            {"new_operations":False,"auto_reconcile":False,"worker_start":False,"identity_rebinding":False}):
        raise BackupError("unsupported_backup_manifest")
    locale=manifest.get("database_locale",{})
    if (set(locale)!={"encoding","collate","ctype","provider"} or locale.get("provider")!="c"
            or any(not isinstance(locale[key],str) or not 1<=len(locale[key])<=128 for key in locale)):
        raise BackupError("unsupported_database_locale")
    actual=file_inventory(root)
    if actual!=manifest.get("files") or "database.dump" not in actual:
        raise BackupError("backup_file_mismatch")
    if "asset_storage" in manifest and not backup_assets.valid_manifest(manifest["asset_storage"], actual):
        raise BackupError("invalid_asset_storage_manifest")
    with checked_path(root,"database.dump").open("rb") as stream:
        if stream.read(5)!=b"PGDMP":
            raise BackupError("invalid_database_dump")
    if manifest["scope"] == CONTAINER_SCOPE:
        from app.maintenance.container_backup import verify_accounts
        verify_accounts(root, manifest, actual)
    elif manifest["native_threads"]:
        native=verify_snapshot(root/"native",version=manifest["codex_version"])
        if sorted(native["threads"])!=manifest["native_threads"]:
            raise BackupError("native_dependency_mismatch")
    elif any(key.startswith("native/") for key in actual):
        raise BackupError("unexpected_native_data")
    return manifest


async def create_bundle(db, settings, destination, roots, *, codex_home=None, pg_dump="pg_dump",
                        writers_stopped=False, dump=dump_database):
    if not writers_stopped:
        raise BackupError("writers_stopped_acknowledgement_required")
    if not settings.v2_enabled or settings.app_env not in {"development","test"} or settings.v2_runtime_mode!="local":
        raise BackupError("local_trial_only")
    if set(roots)-{"assets","runtime","legacy_generated","legacy_references"} or not {"assets","runtime"}<=set(roots):
        raise BackupError("invalid_storage_roots")
    asset_root = backup_assets.cache_root if settings.v2_asset_backend == "minio" else canonical_root
    roots={key:(asset_root(value) if key == "assets" else canonical_root(value)) for key,value in roots.items()}
    if roots["assets"]!=asset_root(settings.v2_storage_dir) or roots["runtime"]!=canonical_root(settings.v2_runtime_dir):
        raise BackupError("storage_configuration_mismatch")
    home=canonical_root(codex_home) if codex_home is not None else None
    for a,left in roots.items():
        for b,right in roots.items():
            if a!=b and (left.is_relative_to(right) or right.is_relative_to(left)):
                raise BackupError("overlapping_storage_roots")
        if home and (left.is_relative_to(home) or home.is_relative_to(left)):
            raise BackupError("profile_storage_overlap")
    destination=new_destination(destination,[*roots.values(),*([home] if home else [])])
    asset_repository = backup_assets.repository(settings)
    async def read_dependencies(db, roots):
        result = await dependencies(db, roots, asset_repository=asset_repository)
        if result[1] and home is None:
            raise BackupError("codex_home_required")
        return result
    async def write_native(destination, threads):
        if threads:
            create_snapshot(home,destination/"native",threads,version=settings.v2_codex_version)
    return await assemble_bundle(db,settings,destination,roots,pg_dump=pg_dump,dump=dump,
        read_dependencies=read_dependencies,write_native=write_native,scope=SCOPE,extra_manifest={})


async def assemble_bundle(db, settings, destination, roots, *, pg_dump, dump,
                          read_dependencies, write_native, scope, extra_manifest):
    names=await freeze(db)
    before=await table_fingerprints(db,names)
    sequences=await sequence_state(db)
    files,threads,absent=await read_dependencies(db,roots)
    locale=(await db.execute(text("SELECT pg_encoding_to_char(encoding) AS encoding,datcollate AS collate,datctype AS ctype, "
                                  "COALESCE(to_jsonb(d)->>'datlocprovider','c') AS provider "
                                  "FROM pg_database d WHERE datname=current_database()"))).mappings().one()
    if locale["provider"]!="c":
        raise BackupError("unsupported_database_locale_provider")
    locale=dict(locale)
    server_major=int(await db.scalar(text("SHOW server_version_num")))//10000
    snapshot=await db.scalar(text("SELECT pg_export_snapshot()"))
    destination.mkdir(parents=True,mode=0o700)
    write_new(destination/"INCOMPLETE",{"status":"backup_incomplete"})
    # Keep the exporter transaction alive throughout pg_dump and all file copying.
    # Local copies and pg_dump remain synchronous. Remote copies use storage_io,
    # which drains cancellation before the transaction can release its locks.
    dump(pg_dump,settings.database_url,snapshot,destination/"database.dump",server_major)
    copied={}
    total=0
    for relative,(source,expected) in sorted(files.items()):
        total+=source.size if isinstance(source, RemoteAsset) else source.stat().st_size
        if total>MAX_TOTAL_BYTES:
            raise BackupError("backup_too_large")
        actual = (await storage_io(source.copy, destination/relative)
                  if isinstance(source, RemoteAsset) else copy_checked(source,destination/relative))
        if expected and actual!=expected:
            raise BackupError("asset_metadata_mismatch")
        copied[relative]=actual
    await write_native(destination, threads)
    # Detect external filesystem writes despite the operational stop requirement.
    after_files,after_threads,after_absent=await read_dependencies(db,roots)
    if set(after_files)!=set(files) or after_threads!=threads or after_absent!=absent:
        raise BackupError("backup_source_changed")
    for relative,(source,_) in after_files.items():
        current = (await storage_io(source.verify) if isinstance(source, RemoteAsset)
                   else {"bytes": source.stat().st_size, "sha256": file_digest(source)})
        if current != copied[relative]:
            raise BackupError("backup_source_changed")
    if await table_fingerprints(db,names)!=before or await sequence_state(db)!=sequences:
        raise BackupError("database_snapshot_changed")
    manifest={"format":1,"scope":scope,"schema":V2_SCHEMA_VERSION,"created_at":utc_now().isoformat(),
        "database_identity":await database_identity(db),"postgres_major":server_major,"database_locale":locale,
        "codex_version":settings.v2_codex_version,"model":DESIGN_MODEL,"tables":before,
        "native_threads":threads,"sequences":sequences,"absent_assets":absent,"files":file_inventory(destination),
        "restore_policy":{"new_operations":False,"auto_reconcile":False,"worker_start":False,"identity_rebinding":False}}
    if set(extra_manifest) & set(manifest):
        raise BackupError("duplicate_manifest_fields")
    manifest.update(extra_manifest)
    manifest.update(backup_assets.source_manifest(settings, files))
    write_new(destination/"manifest.json",manifest)
    verify_bundle(destination,allow_incomplete=True)
    (destination/"INCOMPLETE").unlink()
    return {"status":"created","manifest_sha256":file_digest(destination/"manifest.json"),
            "files":len(manifest["files"]),"tables":len(before),"native_threads":len(threads)}


async def run(args):
    if args.action=="verify":
        manifest=verify_bundle(args.directory,expected_manifest_sha256=args.expected_manifest_sha256)
        return {"status":"verified","files":len(manifest["files"]),"tables":len(manifest["tables"])}
    if args.env_file is None or not args.env_file.is_file():
        raise BackupError("configuration_file_missing")
    settings=Settings(_env_file=args.env_file)
    if settings.v2_runtime_mode == "container" and args.codex_home is not None:
        raise BackupError("container_profiles_are_account_bound")
    roots={"assets":Path(settings.v2_storage_dir)}
    if settings.v2_runtime_mode == "local":
        roots["runtime"] = Path(settings.v2_runtime_dir)
    if args.legacy_generated: roots["legacy_generated"]=args.legacy_generated
    if args.legacy_references: roots["legacy_references"]=args.legacy_references
    engine=create_async_engine(settings.database_url,hide_parameters=True)
    try:
        await require_v2_schema(settings=settings,engine=engine)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        async with factory.begin() as db:
            if settings.v2_runtime_mode == "container":
                from app.maintenance.container_backup import create_container_bundle
                return await create_container_bundle(db,settings,args.directory,roots,
                    pg_dump=args.pg_dump,writers_stopped=args.writers_stopped)
            return await create_bundle(db,settings,args.directory,roots,codex_home=args.codex_home,
                                       pg_dump=args.pg_dump,writers_stopped=args.writers_stopped)
    finally:
        await engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["create","verify"])
    parser.add_argument("directory",type=Path)
    parser.add_argument("--env-file",type=Path)
    parser.add_argument("--codex-home",type=Path)
    parser.add_argument("--legacy-generated",type=Path)
    parser.add_argument("--legacy-references",type=Path)
    parser.add_argument("--pg-dump",default="pg_dump")
    parser.add_argument("--writers-stopped",action="store_true")
    parser.add_argument("--expected-manifest-sha256")
    args=parser.parse_args()
    try:
        result=asyncio.run(run(args))
    except Exception as exc:
        code=str(exc) if isinstance(exc,(BackupError,SnapshotError,CleanupError)) else "backup_unavailable"
        print(json.dumps({"status":"refused","code":code}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__=="__main__":
    main()
