from .legacy_migration_seed import setup, historical_rows, operation
import os,subprocess,sys
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.exc import DBAPIError
from app.design.models import RecoveryCheck
from .test_migrations import migration_database,migrate,ROOT
from .test_revision_numbers import migrate_to

async def test_0016_preserves_21_tables_and_guards_recovery_audit(migration_database):
    assert migrate_to(migration_database,"v2_0015").returncode==0
    engine=create_async_engine(migration_database);factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        owner,_,branch=await setup(factory)
        # Seed a pre-0018 operation: that historical schema had no admission records.
        opid=await operation(factory,owner,branch)
        async with factory() as db:
            names=list((await db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'v2_%' ORDER BY tablename"))).all())
        async def snapshot():
            async with factory() as db:
                return {name:await historical_rows(db,name) for name in names}
        before=await snapshot()
        assert migrate(migration_database).returncode==0
        assert await snapshot()==before and len(names)==21
        async with factory.begin() as db:
            db.add(RecoveryCheck(operation_id=opid,attempt=1,worker_id="migration-qa"))
        for query in ("DELETE FROM v2_recovery_checks","UPDATE v2_recovery_checks SET attempt=2"):
            with pytest.raises(DBAPIError):
                async with factory.begin() as db:await db.execute(text(query))
        result=subprocess.run([sys.executable,"-c","from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0015')"],
            cwd=ROOT,env={**os.environ,"V2_DATABASE_URL":migration_database,"PYTHONIOENCODING":"utf-8"},capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode!=0 and "Refuse to discard automatic recovery audit" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
            assert await db.scalar(text("SELECT count(*) FROM v2_recovery_checks"))==1
    finally:await engine.dispose()
