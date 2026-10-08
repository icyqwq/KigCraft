from .legacy_migration_seed import setup, operation
import os,subprocess,sys
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from .test_migrations import migration_database,migrate,ROOT
from .test_revision_numbers import migrate_to

async def test_legacy_receipts_stay_unknown_and_new_receipts_block_downgrade(migration_database):
    assert migrate_to(migration_database,"v2_0022").returncode==0
    engine=create_async_engine(migration_database);factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        owner,_,branch=await setup(factory)
        opid=await operation(factory,owner,branch)
        async with factory.begin() as db:
            await db.execute(text("INSERT INTO v2_runtime_observations "
                "(id,operation_id,phase,fencing_token,model,codex_version,state,thread_id,token_status,image_items,created_at,updated_at) "
                "VALUES (:id,:op,'generation',1,'model','version','started','private-thread','unknown','{}',now(),now())"),
                {"id":str(uuid4()),"op":opid})
            before=await db.scalar(text("SELECT (to_jsonb(t)-\'trash_restore_until\')::text FROM v2_runtime_observations t"))
        assert migrate(migration_database).returncode==0
        async with factory() as db:
            assert await db.scalar(text("SELECT image_anomalies FROM v2_runtime_observations")) is None
            assert await db.scalar(text("SELECT (to_jsonb(t)-'image_anomalies')::text FROM v2_runtime_observations t"))==before
        async with factory.begin() as db:
            await db.execute(text("UPDATE v2_runtime_observations SET image_anomalies='{\"replayed\":[],\"conflicting\":[]}'"))
        result=subprocess.run([sys.executable,"-c",
            "from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0022')"],
            cwd=ROOT,env={**os.environ,"V2_DATABASE_URL":migration_database},capture_output=True,text=True,timeout=30)
        assert result.returncode!=0 and "Refuse to discard image anomaly observations" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
    finally:await engine.dispose()
