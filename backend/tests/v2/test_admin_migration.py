from .legacy_migration_seed import setup, historical_rows
import os,subprocess,sys
from sqlalchemy import text,select
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.auth.models import UserRole
from app.db.models import User
from app.admin.v2_service import Command,submit_command
from .test_migrations import migration_database,migrate,ROOT
from .test_revision_numbers import migrate_to

async def test_0015_preserves_all_existing_tables_and_refuses_audit_loss(migration_database):
    assert migrate_to(migration_database,"v2_0014").returncode==0
    engine=create_async_engine(migration_database);factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        owner,_,_=await setup(factory)
        async with factory() as db:
            names=list((await db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'v2_%' ORDER BY tablename"))).all())
        async def snapshot():
            async with factory() as db:
                return {name:await historical_rows(db,name) for name in names}
        before=await snapshot()
        assert migrate(migration_database).returncode==0
        assert await snapshot()==before and len(names)==19
        async with factory.begin() as db:
            (await db.get(User,owner)).role=UserRole.admin
            await submit_command(db,owner,Command(action="pause",reason="migration QA control",client_request_id="migration-pause"))
        result=subprocess.run([sys.executable,"-c","from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0014')"],
            cwd=ROOT,env={**os.environ,"V2_DATABASE_URL":migration_database,"PYTHONIOENCODING":"utf-8"},capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode!=0 and "Refuse to discard audit or runtime controls" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
            assert await db.scalar(text("SELECT count(*) FROM v2_admin_actions"))==1
    finally:await engine.dispose()
