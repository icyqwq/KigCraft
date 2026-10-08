"""Database allocation covers every writer, including direct/legacy inserts."""
import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.time import utc_now
from app.db.models import User
from app.design.models import Asset, Branch, Design, Revision
from app.design.service import create_design
from .test_operations import factory, setup
from .test_migrations import migration_database, migrate, ROOT


async def seeds(factory):
    owner, design, branch = await setup(factory)
    async with factory.begin() as db:
        asset = Asset(owner_id=owner, object_key=str(uuid4()), sha256="a"*64,
            mime_type="image/png", kind="reference", width=16, height=16, byte_size=100)
        other = Branch(design_id=design)
        db.add_all([asset, other])
        await db.flush()
        return owner, design, [branch, other.id], asset.id


def revision(design, branch, asset, **values):
    return Revision(design_id=design, branch_id=branch, clean_asset_id=asset,
                    root_source_asset_id=asset, source_kind="manual", **values)


async def test_parallel_branches_share_design_sequence_and_other_design_starts_at_one(factory):
    owner, design, branches, asset = await seeds(factory)
    async def write(index):
        async with factory.begin() as db:
            item = revision(design, branches[index % 2], asset)
            db.add(item)
            await db.flush()
            assert isinstance(item.number, int)  # INSERT RETURNING sees trigger allocation.
            return item.number
    assert sorted(await asyncio.gather(*(write(i) for i in range(12)))) == list(range(1, 13))
    async with factory.begin() as db:
        other = await create_design(db, owner, "another-design", "Other")
        item = revision(other.id, other.last_branch_id, asset)
        db.add(item)
        await db.flush()
        assert item.number == 1
    async with factory() as db:
        assert (await db.get(Design, design)).revision_counter == 12


async def test_rollback_is_atomic_and_number_is_not_timestamp_or_ancestry(factory):
    _, design, branches, asset = await seeds(factory)
    with pytest.raises(RuntimeError, match="abort"):
        async with factory.begin() as db:
            item = revision(design, branches[0], asset)
            db.add(item)
            await db.flush()
            assert item.number == 1
            raise RuntimeError("abort")
    async with factory.begin() as db:
        first = revision(design, branches[0], asset)
        db.add(first)
        await db.flush()
        second = revision(design, branches[1], asset, created_at=utc_now()-timedelta(days=3))
        db.add(second)
        await db.flush()
        assert (first.number, second.number) == (1, 2)
        assert second.created_at < first.created_at and second.parent_revision_id is None
    async with factory() as db:
        assert (await db.get(Design, design)).revision_counter == 2


async def test_numbers_are_server_assigned_immutable_and_safe_for_javascript(factory):
    _, design, branches, asset = await seeds(factory)
    with pytest.raises(DBAPIError, match="assigned by the server"):
        async with factory.begin() as db:
            db.add(revision(design, branches[0], asset, number=9))
            await db.flush()
    async with factory.begin() as db:
        item = revision(design, branches[0], asset)
        db.add(item)
        await db.flush()
        assert item.number == 1
    with pytest.raises(DBAPIError):
        async with factory.begin() as db:
            await db.execute(text("UPDATE v2_revisions SET number=99 WHERE id=:id"), {"id":item.id})
    async with factory.begin() as db:
        await db.execute(text("UPDATE v2_designs SET revision_counter=9007199254740991 WHERE id=:id"), {"id":design})
    with pytest.raises(DBAPIError, match="ck_v2_design_revision_counter"):
        async with factory.begin() as db:
            db.add(revision(design, branches[1], asset))
            await db.flush()


def migrate_to(url, target):
    return subprocess.run([sys.executable, "-c",
        "from alembic import command; from app.db.migrate import migration_config; "
        f"command.upgrade(migration_config(), {target!r})"],
        cwd=ROOT, env={**os.environ,"V2_DATABASE_URL":url,"PYTHONIOENCODING":"utf-8"},
        capture_output=True,text=True,encoding="utf-8",timeout=30)


async def test_populated_migration_backfills_without_changing_existing_fields(migration_database):
    result = migrate_to(migration_database, "v2_0008")
    assert result.returncode == 0, result.stderr
    engine = create_async_engine(migration_database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as db:
            user = User(email=f"{uuid4()}@example.com")
            db.add(user)
            await db.flush()
            # Raw legacy shapes intentionally do not depend on the new ORM columns.
            await db.execute(text("""
                INSERT INTO v2_designs (id,owner_id,client_request_id,request_digest,title,
                    metadata_version,event_sequence,pinned,created_at,updated_at)
                VALUES ('d',:owner,'request','digest','Existing',3,7,false,now(),now())
            """), {"owner":user.id})
            await db.execute(text("""
                INSERT INTO v2_branches (id,design_id,state_version,created_at)
                VALUES ('b1','d',4,now()),('b2','d',2,now())
            """))
            await db.execute(text("""
                INSERT INTO v2_assets (id,owner_id,object_key,sha256,mime_type,kind,width,height,byte_size,status,created_at)
                VALUES ('a',:owner,'asset.png',:sha,'image/png','reference',16,16,100,'ready',now())
            """), {"owner":user.id,"sha":"a"*64})
            # Tie timestamps; the parent id sorts after the child. Numbering is not ancestry.
            await db.execute(text("""
                INSERT INTO v2_revisions (id,design_id,branch_id,parent_revision_id,clean_asset_id,
                    root_source_asset_id,source_kind,view,created_at)
                VALUES ('r-z','d','b1',NULL,'a','a','manual','front','2026-01-01'),
                       ('r-a','d','b2','r-z','a','a','manual','front','2026-01-01')
            """))
        async def snapshot():
            async with engine.connect() as db:
                return {table:[dict(r) for r in (await db.execute(text(f"SELECT * FROM {table} ORDER BY id"))).mappings()]
                        for table in ("v2_designs","v2_branches","v2_revisions")}
        before = await snapshot()
        result = migrate_to(migration_database, "v2_0009")
        assert result.returncode == 0, result.stderr
        await engine.dispose()  # Schema changed in a separate migration process.
        after = await snapshot()
        assert [(r["id"],r.pop("number")) for r in after["v2_revisions"]] == [("r-a",1),("r-z",2)]
        assert after["v2_designs"][0].pop("revision_counter") == 2
        assert before == after
        assert migrate_to(migration_database, "v2_0009").returncode == 0
        assert migrate(migration_database).returncode == 0
        await engine.dispose()
        async with sessions.begin() as db:
            item = revision("d","b1","a")
            db.add(item)
            await db.flush()
            assert item.number == 3
        with pytest.raises(DBAPIError):
            async with engine.begin() as db:
                await db.execute(text("UPDATE v2_revisions SET source_kind='ai' WHERE id='r-a'"))
        with pytest.raises(DBAPIError):
            async with engine.begin() as db:
                await db.execute(text("DELETE FROM v2_revisions WHERE id='r-a'"))
    finally:
        await engine.dispose()
