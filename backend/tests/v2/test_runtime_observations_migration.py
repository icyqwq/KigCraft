from .legacy_migration_seed import setup, operation
import os,subprocess,sys
from sqlalchemy import text,select
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.design.models import RuntimeObservation
from app.operations.observations import record
from .test_migrations import migration_database,migrate,ROOT
from .test_revision_numbers import migrate_to
from .legacy_migration_seed import fingerprints

async def test_0019_preserves_rows_and_blocks_observation_loss(migration_database):
    assert migrate_to(migration_database,"v2_0018").returncode==0
    engine=create_async_engine(migration_database);factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        owner,_,branch=await setup(factory)
        opid=await operation(factory,owner,branch)
        before=await fingerprints(factory)
        assert migrate(migration_database).returncode==0
        assert migrate(migration_database).returncode==0
        after=await fingerprints(factory)
        assert all(after[name]==value for name,value in before.items() if name!="alembic_version")
        async with factory.begin() as db:
            await record(db,opid,1,"review","review-model","version",{"kind":"dispatch","thread_id":"thread"})
            # Model a pre-0023 observation so this test reaches the original 0019 guard.
            (await db.scalar(select(RuntimeObservation))).image_anomalies = None
        env={**os.environ,"V2_DATABASE_URL":migration_database,"PYTHONIOENCODING":"utf-8"}
        result=subprocess.run([sys.executable,"-c",
            "from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0018')"],
            cwd=ROOT,env=env,capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode!=0 and "Refuse to discard runtime observations" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
            assert (await db.scalar(select(RuntimeObservation))).state=="dispatch_intent"
    finally:await engine.dispose()
