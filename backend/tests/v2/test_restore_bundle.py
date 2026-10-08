"""Restore orchestration checks with database creation/import substituted."""
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from app.admin.v2_controls import gate
from app.design.models import RuntimeControl
from app.design.service import ApplicationError
from app.maintenance import backup_bundle as backup
from app.maintenance import restore_bundle as restore
from app.maintenance.backup_bundle import BackupError
from .test_migrations import migration_database
from .test_operations import factory
from .test_backup_bundle import prepared, create
from .test_legacy_inventory import fingerprints

async def fake_create(url,name,major,locale):
    assert set(locale)=={"encoding","collate","ctype","provider"}
    assert name.startswith("kig_v2_restore_") and major>=14
    return make_url("postgresql+asyncpg://test@localhost/"+name)

def fake_import(executable,url,source,major):
    assert source.read_bytes().startswith(b"PGDMP")

async def fake_validate(url,manifest):
    return {"global_pause":True,"quarantined_accounts":1,"changed_tables":["v2_runtime_controls"],"operations_requeued":False}

async def test_restore_files_to_new_directory_and_emit_execution_disabled_configuration(factory,tmp_path):
    _,_,_,roots,settings=await prepared(factory,tmp_path)
    source=tmp_path/"backup"
    result=await create(factory,settings,source,roots)
    destination=tmp_path/"restored"
    before=await fingerprints(factory)
    output=await restore.restore_bundle(source,destination,"postgresql+asyncpg://admin@localhost/postgres",
        result["manifest_sha256"],create_database=fake_create,restore=fake_import,validate=fake_validate)
    assert output["status"]=="restored_execution_disabled"
    assert output["database"].startswith("kig_v2_restore_")
    receipt=json.loads((destination/"restore-target.json").read_text(encoding="utf-8"))
    assert receipt["database"]==output["database"] and receipt["worker_start"] is False
    for relative in backup.verify_bundle(source)["files"]:
        if relative!="database.dump":
            assert (source/relative).read_bytes()==(destination/relative).read_bytes()
    config=(destination/"execution-disabled.env.example").read_text(encoding="utf-8")
    assert 'V2_NEW_OPERATIONS="false"' in config
    assert 'V2_AUTO_RECONCILE_ENABLED="false"' in config
    assert 'V2_RUNTIME_MODE="disabled"' in config
    assert "admin@" not in config and not (destination/"INCOMPLETE").exists()
    assert await fingerprints(factory)==before

async def test_failed_creation_keeps_planned_database_identity(factory,tmp_path):
    _,_,_,roots,settings=await prepared(factory,tmp_path)
    source=tmp_path/"backup"
    result=await create(factory,settings,source,roots)
    called=[]
    async def failed(url,name,major,locale):
        called.append(name)
        raise BackupError("simulated_connection_loss")
    destination=tmp_path/"restored"
    with pytest.raises(BackupError,match="simulated_connection_loss"):
        await restore.restore_bundle(source,destination,"postgresql+asyncpg://admin@localhost/postgres",
            result["manifest_sha256"],create_database=failed,restore=fake_import,validate=fake_validate)
    assert (destination/"INCOMPLETE").is_file()
    assert json.loads((destination/"restore-target.json").read_text())["database"]==called[0]
    with pytest.raises(BackupError,match="new_absolute"):
        await restore.restore_bundle(source,destination,"postgresql+asyncpg://admin@localhost/postgres",
            result["manifest_sha256"],create_database=failed,restore=fake_import,validate=fake_validate)
    assert len(called)==1

async def test_guard_verifies_data_first_then_blocks_every_existing_account(factory,tmp_path):
    owner,_,_,roots,settings=await prepared(factory,tmp_path)
    source=tmp_path/"backup"
    await create(factory,settings,source,roots)
    manifest=backup.verify_bundle(source)
    before=await fingerprints(factory)
    async with factory.begin() as db:
        result=await restore.verify_and_guard(db,manifest)
    after=await fingerprints(factory)
    assert {k:v for k,v in after.items() if k!="v2_runtime_controls"}=={k:v for k,v in before.items() if k!="v2_runtime_controls"}
    assert result["quarantined_accounts"]==1 and result["operations_requeued"] is False
    async with factory() as db:
        assert (await db.get(RuntimeControl,"owner:"+owner)).blocked
        with pytest.raises(ApplicationError,match="runtime_quarantined"):
            await gate(db,owner,recovery=True)

async def test_wrong_fingerprint_does_not_install_guards(factory,tmp_path):
    _,_,_,roots,settings=await prepared(factory,tmp_path)
    source=tmp_path/"backup"
    await create(factory,settings,source,roots)
    manifest=backup.verify_bundle(source)
    manifest["tables"]["users"]["sha256"]="0"*64
    before=await fingerprints(factory)
    with pytest.raises(BackupError,match="restored_database_mismatch"):
        async with factory.begin() as db:
            await restore.verify_and_guard(db,manifest)
    assert await fingerprints(factory)==before

def test_pg_restore_uses_single_transaction_and_never_clean_or_create_flags(monkeypatch,tmp_path):
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        return SimpleNamespace(returncode=0,stdout=b"pg_restore (PostgreSQL) 14.24\n")
    monkeypatch.setattr(subprocess,"run",run)
    restore.restore_database("pg_restore","postgresql+asyncpg://admin:synthetic@localhost/kig_v2_restore_test",
                             tmp_path/"database.dump",14)
    command,kwargs=calls[1]
    assert "--single-transaction" in command and "--exit-on-error" in command
    assert "--clean" not in command and "--create" not in command
    assert "synthetic" not in str(command) and kwargs["env"]["PGPASSWORD"]=="synthetic"


async def test_database_creation_checks_server_version_and_never_reuses_or_drops(monkeypatch):
    from contextlib import asynccontextmanager
    statements=[]
    class Connection:
        async def scalar(self,query):
            if str(query).startswith("SHOW"):
                return "140024"
            value=next(iter(query.compile().params.values()))
            return "'"+value.replace("'","''")+"'"
        async def execute(self,query):
            statements.append(str(query))
    class Engine:
        @asynccontextmanager
        async def connect(self):
            yield Connection()
        async def dispose(self):
            pass
    monkeypatch.setattr(restore,"create_async_engine",lambda *a,**kw:Engine())
    locale={"encoding":"UTF8","collate":"C","ctype":"C","provider":"c"}
    name="kig_v2_restore_"+"a"*32
    with pytest.raises(BackupError,match="restore_server_version_mismatch"):
        await restore.create_target_database("postgresql://admin@localhost/postgres",name,15,locale)
    assert statements==[]
    result=await restore.create_target_database("postgresql://admin@localhost/postgres",name,14,locale)
    assert result.database==name and result.drivername=="postgresql+asyncpg"
    assert len(statements)==1 and statements[0].startswith('CREATE DATABASE "'+name+'" TEMPLATE template0')
    assert "ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'" in statements[0]
    assert "DROP" not in statements[0]
    with pytest.raises(BackupError,match="invalid_restore_database_name"):
        await restore.create_target_database("postgresql://admin@localhost/postgres","existing_database",14,locale)
    assert len(statements)==1
