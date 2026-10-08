"""Real PostgreSQL gates; opt in with an isolated KIG_V2_TEST_DATABASE_URL."""
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4
import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def migration_database():
    value = os.environ.get("KIG_V2_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set KIG_V2_TEST_DATABASE_URL to an isolated local PostgreSQL test cluster")
    url = make_url(value)
    if url.host not in {"127.0.0.1", "localhost"} or not (url.database or "").startswith("kig_v2_"):
        pytest.fail("Migration tests require a local kig_v2_ test database")
    admin = await asyncpg.connect(str(url.set(drivername="postgresql", database="postgres")))
    name = "kig_v2_test_" + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    target = url.set(database=name)
    try:
        yield target.render_as_string(hide_password=False)
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


def migrate(url, action="upgrade"):
    env = {**os.environ, "V2_DATABASE_URL": url, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "app.db.migrate", action], cwd=ROOT,
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)


async def tables(url):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return set((await connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            ))).scalars())
    finally:
        await engine.dispose()


async def create_legacy(url):
    # Import the V2 models first: old startup must STILL not create any V2 table.
    from app.design import models as _v2_models
    from app.db import models as _models
    from app.db.base import Base
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda conn: Base.metadata.create_all(
                conn, tables=[t for t in Base.metadata.sorted_tables if not t.info.get("v2")]
            ))
    finally:
        await engine.dispose()


async def test_empty_install_and_repeat_upgrade(migration_database):
    result = migrate(migration_database)
    assert result.returncode == 0, result.stderr
    assert {"users", "v2_designs", "v2_ledger", "v2_events"} <= await tables(migration_database)
    assert migrate(migration_database).returncode == 0
    engine = create_async_engine(migration_database)
    try:
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == "v2_0029"
    finally:
        await engine.dispose()


async def test_existing_legacy_requires_validated_baseline(migration_database):
    await create_legacy(migration_database)
    assert not any(t.startswith("v2_") for t in await tables(migration_database))
    refused = migrate(migration_database)
    assert refused.returncode != 0
    assert "verified baseline" in refused.stderr
    result = migrate(migration_database, "baseline")
    assert result.returncode == 0, result.stderr
    result = migrate(migration_database)
    assert result.returncode == 0, result.stderr


async def test_schema_drift_refuses_stamp(migration_database):
    await create_legacy(migration_database)
    engine = create_async_engine(migration_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE users ADD COLUMN unexpected_drift TEXT"))
    finally:
        await engine.dispose()
    result = migrate(migration_database, "baseline")
    assert result.returncode != 0
    assert "column drift" in result.stderr
    assert "alembic_version" not in await tables(migration_database)


async def test_two_migration_processes_serialize(migration_database):
    import asyncio
    first, second = await asyncio.gather(
        asyncio.to_thread(migrate, migration_database),
        asyncio.to_thread(migrate, migration_database),
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
