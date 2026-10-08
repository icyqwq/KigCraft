from .legacy_migration_seed import setup, operation
import os
import subprocess
import sys
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.design.models import AssetReviewCache
from app.core.time import utc_now
from .test_migrations import migration_database, migrate, ROOT
from .test_revision_numbers import migrate_to
from .legacy_migration_seed import fingerprints


async def test_0020_preserves_existing_rows_and_refuses_cached_evidence_loss(migration_database):
    assert migrate_to(migration_database,"v2_0019").returncode==0
    engine=create_async_engine(migration_database)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        owner,_,branch=await setup(factory)
        opid=await operation(factory,owner,branch)
        before=await fingerprints(factory)
        assert migrate(migration_database).returncode==0
        assert migrate(migration_database).returncode==0
        after=await fingerprints(factory)
        assert all(after[name]==value for name,value in before.items() if name!="alembic_version")
        async with factory.begin() as db:
            db.add(AssetReviewCache(owner_id=owner,sha256="a"*64,policy_key="b"*64,verdict="allow",
                source_operation_id=opid,created_at=utc_now()))
        result=subprocess.run([sys.executable,"-c",
            "from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0019')"],
            cwd=ROOT,env=os.environ|{"V2_DATABASE_URL":migration_database,"PYTHONIOENCODING":"utf-8"},
            capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode!=0 and "Refuse to discard asset review evidence" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
            assert (await db.scalar(select(AssetReviewCache))).verdict=="allow"
    finally:
        await engine.dispose()
