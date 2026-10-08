from .legacy_migration_seed import setup, operation
import os, subprocess, sys, hashlib
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.time import utc_now
from app.design.models import ContextObservation
from .test_migrations import migration_database, migrate, ROOT
from .test_revision_numbers import migrate_to
from .legacy_migration_seed import fingerprints


async def test_context_migration_preserves_business_rows_and_refuses_receipt_loss(migration_database):
    assert migrate_to(migration_database,"v2_0021").returncode==0
    engine=create_async_engine(migration_database)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        owner,_,branch=await setup(factory)
        opid=await operation(factory,owner,branch)
        before=await fingerprints(factory)
        result=migrate(migration_database)
        assert result.returncode==0,result.stderr
        after=await fingerprints(factory)
        assert all(after[name]==rows for name,rows in before.items() if name!="alembic_version")
        assert after["v2_context_observations"]==hashlib.sha256(b"").hexdigest()
        async with factory.begin() as db:
            db.add(ContextObservation(operation_id=opid,fencing_token=1,method="resume",state="confirmed",completed_at=utc_now()))
        result=subprocess.run([sys.executable,"-c",
            "from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0021')"],
            cwd=ROOT,env={**os.environ,"V2_DATABASE_URL":migration_database},capture_output=True,text=True,timeout=30)
        assert result.returncode!=0 and "Refuse to discard context observations" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
            assert await db.scalar(text("SELECT count(*) FROM v2_context_observations"))==1
    finally:await engine.dispose()
