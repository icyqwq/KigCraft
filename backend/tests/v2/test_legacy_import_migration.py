import os,subprocess,sys
import pytest
from sqlalchemy import select,text,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.design.models import LegacyImport
from .test_migrations import migration_database,migrate,ROOT
from .test_revision_numbers import migrate_to
from .test_legacy_import import prepared,apply
from .test_legacy_inventory import fingerprints

async def test_0017_preserves_all_existing_rows_and_refuses_provenance_loss(migration_database,tmp_path):
    assert migrate_to(migration_database,"v2_0016").returncode==0
    engine=create_async_engine(migration_database);factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        ids,report=await prepared(factory,tmp_path/"old")
        before=await fingerprints(factory)
        assert len([name for name in before if name.startswith("v2_")])==22
        assert migrate(migration_database).returncode==0
        after=await fingerprints(factory)
        assert all(after[name]==value for name,value in before.items() if name!="alembic_version")
        await apply(factory,ids,report,tmp_path/"old",tmp_path/"new")
        result=subprocess.run([sys.executable,"-c","from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0016')"],
            cwd=ROOT,env={**os.environ,"V2_DATABASE_URL":migration_database},capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode!=0 and "Refuse to discard legacy import provenance" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
            assert await db.scalar(select(func.count()).select_from(LegacyImport))==5
    finally:await engine.dispose()
