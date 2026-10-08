"""Restore a trusted backup into a new database and directory, with execution disabled."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import User
from app.db.v2_schema import require_v2_schema
from app.design.models import RuntimeControl
from app.maintenance.backup_bundle import (
    CONTAINER_SCOPE, BackupError, copy_checked, new_destination, pg_environment, sequence_state,
    table_fingerprints, verify_bundle,
)
from app.maintenance.codex_snapshot import checked_path, digest, restore_snapshot, SnapshotError
from app.maintenance.orphan_assets import CleanupError, root_path, write_new

class RestoreConfiguration(BaseSettings):
    model_config=SettingsConfigDict(env_file=None,extra="ignore")
    admin_database_url:str=Field(validation_alias="V2_RESTORE_ADMIN_DATABASE_URL")


async def create_target_database(admin_database_url, name, server_major, locale):
    if not re.fullmatch(r"kig_v2_restore_[0-9a-f]{32}",name):
        raise BackupError("invalid_restore_database_name")
    url=make_url(admin_database_url)
    pg_environment(admin_database_url)  # Validate supported connection options first.
    url=url.set(drivername="postgresql+asyncpg")
    engine=create_async_engine(url,isolation_level="AUTOCOMMIT",hide_parameters=True)
    try:
        async with engine.connect() as db:
            if int(await db.scalar(text("SHOW server_version_num")))//10000!=server_major:
                raise BackupError("restore_server_version_mismatch")
            # Generated identifier only. Never DROP, CLEAN, overwrite or reuse a database.
            literals={key:await db.scalar(select(func.quote_literal(locale[key]))) for key in ("encoding","collate","ctype")}
            await db.execute(text('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING '+literals["encoding"]+
                                  ' LC_COLLATE '+literals["collate"]+' LC_CTYPE '+literals["ctype"]))
    finally:
        await engine.dispose()
    return url.set(database=name)


def restore_database(executable, target_url, source, server_major):
    env=pg_environment(target_url)
    flags={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
    probe=subprocess.run([str(executable),"--version"],env=env,stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL,timeout=15,check=False,**flags)
    match=re.fullmatch(rb"pg_restore \(PostgreSQL\) (\d+)(?:\.[^\r\n]+)?\s*",probe.stdout)
    if probe.returncode or not match or int(match[1])!=server_major:
        raise BackupError("pg_restore_version_mismatch")
    result=subprocess.run([str(executable),"-w","--single-transaction","--exit-on-error",
        "--no-owner","--no-acl","--dbname",make_url(target_url).database,str(source)],
        env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3600,check=False,**flags)
    if result.returncode:
        raise BackupError("database_restore_failed")


async def verify_and_guard(db, manifest):
    await db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    names=list(await db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")))
    if set(names)!=set(manifest["tables"]):
        raise BackupError("restored_catalog_mismatch")
    if await table_fingerprints(db,names)!=manifest["tables"] or await sequence_state(db)!=manifest["sequences"]:
        raise BackupError("restored_database_mismatch")
    # After proving the restored data matches, explicitly isolate every account.
    # Recovery gates check owner quarantine even when they bypass global pause.
    owners=list(await db.scalars(select(User.id)))
    for scope in ["global",*("owner:"+identity for identity in owners)]:
        stmt=insert(RuntimeControl).values(scope=scope,blocked=True)
        await db.execute(stmt.on_conflict_do_update(index_elements=[RuntimeControl.scope],set_={"blocked":True}))
    return {"global_pause":True,"quarantined_accounts":len(owners),
            "changed_tables":["v2_runtime_controls"],"operations_requeued":False}


async def validate_target(target_url,manifest):
    engine=create_async_engine(target_url,hide_parameters=True)
    try:
        settings=Settings(_env_file=None,v2_enabled=True,v2_runtime_mode="disabled",
                          database_url=make_url(target_url).render_as_string(hide_password=False))
        await require_v2_schema(settings=settings,engine=engine)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        async with factory.begin() as db:
            return await verify_and_guard(db,manifest)
    finally:
        await engine.dispose()


async def restore_bundle(source,destination,admin_database_url,expected_manifest_sha256,
                         *,pg_restore="pg_restore",create_database=create_target_database,
                         restore=restore_database,validate=validate_target):
    if not expected_manifest_sha256 or not re.fullmatch(r"[0-9a-f]{64}",expected_manifest_sha256):
        raise BackupError("reviewed_manifest_hash_required")
    source=root_path(Path(source))
    manifest=verify_bundle(source,expected_manifest_sha256=expected_manifest_sha256)
    destination=new_destination(Path(destination),[source])
    pg_environment(admin_database_url)
    destination.mkdir(parents=True,mode=0o700)
    write_new(destination/"INCOMPLETE",{"status":"restore_incomplete"})
    name="kig_v2_restore_"+uuid4().hex
    # Persist the generated name before CREATE so even ambiguous connection loss
    # leaves an inspectable target identity. Re-runs never reuse this directory.
    write_new(destination/"restore-target.json",{
        "database":name,"source_manifest_sha256":expected_manifest_sha256,
        "worker_start":False,"new_operations":False})
    target_url=await create_database(admin_database_url,name,manifest["postgres_major"],manifest["database_locale"])
    if make_url(target_url).database!=name:
        raise BackupError("restore_target_identity_mismatch")
    restore(pg_restore,target_url,checked_path(source,"database.dump"),manifest["postgres_major"])
    guards=await validate(target_url,manifest)
    accounts = manifest.get("accounts", {}) if manifest["scope"] == CONTAINER_SCOPE else {}
    native_prefixes = tuple(f"accounts/{owner}/native/" for owner in accounts)
    copied={}
    for relative,expected in manifest["files"].items():
        if relative=="database.dump" or relative.startswith(("native/", *native_prefixes)):
            continue
        actual=copy_checked(checked_path(source,relative),destination/relative)
        if actual!=expected:
            raise BackupError("restored_file_mismatch")
        copied[relative]=actual
    for folder in ("assets","runtime"):
        (destination/folder).mkdir(exist_ok=True)
    if accounts:
        for owner, entry in accounts.items():
            account = destination/"accounts"/owner
            for folder in ("work", "inputs"):
                (account/folder).mkdir(parents=True, exist_ok=True)
            if entry["native_threads"]:
                restore_snapshot(source/"accounts"/owner/"native",account/"profile",version=manifest["codex_version"])
            else:
                (account/"profile").mkdir()
    elif manifest["native_threads"]:
        restore_snapshot(source/"native",destination/"codex-home",version=manifest["codex_version"])
    # No credentials are copied into a runnable configuration. Execution stays
    # disabled even if an operator supplies a read-only API database connection.
    settings={
        "V2_ENABLED":"true","V2_NEW_OPERATIONS":"false","V2_AUTO_RECONCILE_ENABLED":"false",
        "V2_RUNTIME_MODE":"disabled","V2_CODEX_VERSION":manifest["codex_version"],
        "CODEX_UPDATE_ENABLED":"false","CODEX_SESSION_CLEANUP_ENABLED":"false",
        "V2_UPLOAD_RETENTION_DAYS":"0","V2_ASSET_BACKEND":"filesystem","V2_STORAGE_DIR":(destination/"assets").as_posix(),
        "V2_RUNTIME_DIR":(destination/"runtime").as_posix(),
    }
    if accounts:
        settings.update(V2_CONTAINER_ROOT=(destination/"accounts").as_posix(), V2_CONTAINER_USER_IDS="[]")
    content="# Supply a database connection separately; do not start a Worker.\n"
    content+="\n".join(key+"="+json.dumps(value) for key,value in settings.items())+"\n"
    with (destination/"execution-disabled.env.example").open("x",encoding="utf-8") as stream:
        stream.write(content)
    verify_bundle(source,expected_manifest_sha256=expected_manifest_sha256)
    write_new(destination/"restore-receipt.json",{
        "format":1,"source_manifest_sha256":expected_manifest_sha256,"database":make_url(target_url).database,
        "guards":guards,"files":copied,"native_threads":manifest["native_threads"],
        **({"source_asset_storage": manifest["asset_storage"], "restored_asset_backend": "filesystem"}
           if "asset_storage" in manifest else {}),
        "native_history_read_only":True,"runtime_identity_rebound":False,
        **({"accounts": accounts, "source_runtime_configuration": manifest["runtime_configuration"]} if accounts else {})})
    (destination/"INCOMPLETE").unlink()
    return {"status":"restored_execution_disabled","database":make_url(target_url).database,
            "files":len(copied),"native_threads":len(manifest["native_threads"]),"guards":guards}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path)
    parser.add_argument("destination",type=Path)
    parser.add_argument("--env-file",type=Path,required=True)
    parser.add_argument("--expected-manifest-sha256",required=True)
    parser.add_argument("--pg-restore",default="pg_restore")
    args=parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise BackupError("restore_configuration_missing")
        settings=RestoreConfiguration(_env_file=args.env_file)
        result=asyncio.run(restore_bundle(args.source,args.destination,settings.admin_database_url,
                          args.expected_manifest_sha256,pg_restore=args.pg_restore))
    except Exception as exc:
        code=str(exc) if isinstance(exc,(BackupError,SnapshotError,CleanupError)) else "restore_unavailable"
        print(json.dumps({"status":"refused","code":code}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__=="__main__":
    main()
