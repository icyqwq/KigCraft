from .test_operations import quoted, submit_request
from .legacy_migration_seed import setup
import os,subprocess,sys
from sqlalchemy import select,func,text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.design.models import SubmissionAdmission
from .test_migrations import migration_database,migrate,ROOT
from .test_revision_numbers import migrate_to
from .legacy_migration_seed import fingerprints

async def test_0018_preserves_existing_rows_and_refuses_admission_history_loss(migration_database):
    assert migrate_to(migration_database,"v2_0017").returncode==0
    engine=create_async_engine(migration_database);factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        owner,_,branch=await setup(factory)
        before=await fingerprints(factory)
        upgraded=migrate(migration_database)
        assert upgraded.returncode==0,upgraded.stderr
        after=await fingerprints(factory)
        assert all(after[name]==value for name,value in before.items() if name!="alembic_version")
        assert migrate(migration_database).returncode==0
        await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
        result=subprocess.run([sys.executable,"-c",
            "from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0017')"],
            cwd=ROOT,env={**os.environ,"V2_DATABASE_URL":migration_database,"PYTHONIOENCODING":"utf-8"},
            capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode!=0 and "Refuse to discard accepted submission history" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
            assert await db.scalar(select(func.count()).select_from(SubmissionAdmission))==1
    finally:
        await engine.dispose()
