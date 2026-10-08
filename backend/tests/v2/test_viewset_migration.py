from .legacy_migration_seed import setup, historical_rows
import os
from unittest.mock import AsyncMock,patch
import subprocess
import sys
from sqlalchemy import text, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
import pytest
from app.contracts.viewsets import ViewSetRequest,SubmitViewSet,ViewSetOptions
from app.design.models import ViewSet,ViewSetItem,ViewSetAttempt,Revision
from app.operations.viewset_group import create_quote,submit_group
from .test_migrations import migration_database,migrate,ROOT
from .test_revision_numbers import migrate_to
from .test_viewset_inputs import seed


async def test_0014_preserves_old_rows_and_guards_frozen_group_records(migration_database,tmp_path):
    assert migrate_to(migration_database,"v2_0013").returncode==0
    engine=create_async_engine(migration_database);factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        owner,design,branch,source,_=await seed(factory,tmp_path,setup_design=setup,historical=True)
        names=["v2_designs","v2_branches","v2_assets","v2_revisions","v2_wallets","v2_ledger"]
        async def snapshot():
            async with factory() as db:
                return {name:await historical_rows(db,name) for name in names}
        before=await snapshot()
        assert migrate(migration_database).returncode==0
        assert await snapshot()==before
        request=ViewSetRequest(client_request_id="migration-viewset",source_revision_id=source,expected_branch_version=1,
            options=ViewSetOptions(allow_back_inference=True))
        async with factory.begin() as db:
            price=await create_quote(db,owner,branch,request)
            # A 0014-era authorization has no 0018 admission counter. Keep this test focused
            # on the older immutable group guard; the new admission guard has its own test.
            with patch("app.operations.submission_admission.admit",new=AsyncMock()):
                group=await submit_group(db,owner,branch,SubmitViewSet(**request.model_dump(),quote_id=price.id))
            item=await db.scalar(select(ViewSetItem).where(ViewSetItem.viewset_id==group.id,ViewSetItem.view=="front"))
            group_id,item_id=group.id,item.id
        for sql,params in [
            ("UPDATE v2_viewsets SET max_units=99 WHERE id=:id",{"id":group_id}),
            ("UPDATE v2_viewsets SET snapshot='{}' WHERE id=:id",{"id":group_id}),
            ("UPDATE v2_viewset_items SET view='back' WHERE id=:id",{"id":item_id}),
            ("UPDATE v2_viewset_items SET revision_id=NULL WHERE id=:id",{"id":item_id}),
            ("DELETE FROM v2_viewset_items WHERE id=:id",{"id":item_id}),
            ("DELETE FROM v2_viewset_attempts",{}),
        ]:
            with pytest.raises(DBAPIError):
                async with factory.begin() as db:await db.execute(text(sql),params)
        env={**os.environ,"V2_DATABASE_URL":migration_database,"PYTHONIOENCODING":"utf-8"}
        result=subprocess.run([sys.executable,"-c",
            "from alembic import command; from app.db.migrate import migration_config; command.downgrade(migration_config(),'v2_0013')"],
            cwd=ROOT,env=env,capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode!=0 and "Cannot discard view-set authorizations" in result.stderr
        async with factory() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version"))=="v2_0029"
            assert (await db.get(Revision,source)).view=="front"
            assert await db.get(ViewSet,group_id)
    finally:await engine.dispose()
