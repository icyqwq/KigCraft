"""Use migrated PostgreSQL databases; never infer constraint safety from SQLite."""
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db import schema_catalog, v2_schema
from tests.v2.test_migrations import migration_database, migrate


@pytest_asyncio.fixture
async def schema_engine(migration_database):
    result = migrate(migration_database)
    assert result.returncode == 0, result.stderr
    engine = create_async_engine(migration_database)
    try:
        yield engine
    finally:
        await engine.dispose()


def settings():
    return Settings(_env_file=None, v2_enabled=True, app_env="test")


async def catalog(engine):
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        return await schema_catalog.read_catalog(connection)


async def test_fresh_migrations_match_manifest_across_independent_databases(schema_engine):
    expected = json.loads(schema_catalog.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert await catalog(schema_engine) == expected["catalog"]
    verified = await v2_schema.require_v2_schema(settings=settings(), engine=schema_engine)
    assert verified == {"sha256": expected["sha256"], "tables": 47, "postgresql_major": 14}


@pytest.mark.parametrize("statements,section", [
    (["ALTER TABLE v2_runtime_observations DROP COLUMN token_snapshot"], "columns"),
    (["ALTER TABLE v2_ledger ALTER COLUMN balance_delta TYPE bigint"], "columns"),
    (["ALTER TABLE v2_ledger ALTER COLUMN balance_delta DROP NOT NULL"], "columns"),
    (["ALTER TABLE v2_ledger ALTER COLUMN balance_delta SET DEFAULT 0"], "columns"),
    (["ALTER TABLE v2_ledger ADD COLUMN drift_note text DEFAULT 'secret-default-value'"], "columns"),
    (["ALTER TABLE v2_ledger DROP CONSTRAINT ck_v2_ledger_balances"], "constraints"),
    (["ALTER TABLE v2_ledger DROP CONSTRAINT ck_v2_ledger_balances",
      "ALTER TABLE v2_ledger ADD CONSTRAINT ck_v2_ledger_balances CHECK (balance_after >= reserved_after AND reserved_after >= 0) NOT VALID"], "constraints"),
    (["ALTER TABLE v2_ledger DROP CONSTRAINT fk_v2_ledger_operation_owner"], "constraints"),
    (["ALTER TABLE v2_ledger DROP CONSTRAINT uq_v2_ledger_key"], "constraints"),
    (["DROP INDEX uq_v2_active_design_operation"], "indexes"),
    (["CREATE UNIQUE INDEX unexpected_unique ON v2_ledger(balance_delta)"], "indexes"),
    (["ALTER TABLE v2_ledger DISABLE TRIGGER v2_ledger_immutable"], "triggers"),
    (["ALTER TABLE v2_runtime_observations DISABLE TRIGGER ALL"], "triggers"),
    (["CREATE OR REPLACE FUNCTION v2_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"], "functions"),
    (["ALTER TABLE v2_ledger ENABLE ROW LEVEL SECURITY"], "relation"),
    (["CREATE TABLE v2_unexpected (id integer)"], "unexpected_v2_relation"),
    (["ALTER TYPE userrole ADD VALUE 'unexpected_role'"], "enums"),
])
async def test_correct_version_cannot_hide_structural_drift(schema_engine, statements, section):
    async with schema_engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    before = await catalog(schema_engine)
    async with schema_engine.connect() as connection:
        assert (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == "v2_0029"
    with pytest.raises(RuntimeError, match="V2 schema drift") as error:
        await v2_schema.require_v2_schema(settings=settings(), engine=schema_engine)
    assert section in str(error.value)
    assert "secret-default-value" not in str(error.value)
    assert await catalog(schema_engine) == before  # Refusal never repairs the schema.


async def test_extra_nonunique_indexes_and_unrelated_tables_do_not_block_startup(schema_engine):
    async with schema_engine.begin() as connection:
        await connection.execute(text("CREATE INDEX qa_perf_index ON v2_ledger(created_at)"))
        await connection.execute(text("CREATE TABLE unrelated_tooling (id integer)"))
    before = await catalog(schema_engine)
    await v2_schema.require_v2_schema(settings=settings(), engine=schema_engine)
    assert await catalog(schema_engine) == before


async def test_disabled_internal_fk_is_detected_even_when_constraint_is_valid(schema_engine):
    async with schema_engine.begin() as connection:
        trigger = (await connection.execute(text("""
            SELECT tgname FROM pg_trigger WHERE tgrelid='v2_runtime_observations'::regclass
            AND tgisinternal ORDER BY tgname LIMIT 1
        """))).scalar_one()
        await connection.execute(text('ALTER TABLE v2_runtime_observations DISABLE TRIGGER "' + trigger.replace('"','""') + '"'))
        assert (await connection.execute(text("""
            SELECT bool_and(convalidated) FROM pg_constraint
            WHERE conrelid='v2_runtime_observations'::regclass
        """))).scalar_one()
    with pytest.raises(RuntimeError, match="v2_runtime_observations:triggers"):
        await v2_schema.require_v2_schema(settings=settings(), engine=schema_engine)


async def test_api_and_worker_refuse_before_cleanup_or_model_start(schema_engine, monkeypatch):
    from app import main
    from app.operations import worker
    async with schema_engine.begin() as connection:
        await connection.execute(text("DROP TABLE v2_runtime_observations"))
    config = settings()
    monkeypatch.setattr(v2_schema, "get_engine", lambda: schema_engine)
    monkeypatch.setattr(v2_schema, "get_settings", lambda: config)
    monkeypatch.setattr(main, "get_settings", lambda: config)
    monkeypatch.setattr(worker, "get_settings", lambda: config)
    def unexpected(*args, **kwargs):
        pytest.fail("Worker started before schema validation")
    monkeypatch.setattr(worker, "LocalWorker", unexpected)
    with pytest.raises(RuntimeError, match="v2_runtime_observations:missing"):
        async with main._lifespan(None):
            pytest.fail("Drifted API started")
    with pytest.raises(RuntimeError, match="v2_runtime_observations:missing"):
        await worker.run(SimpleNamespace(once=True, retry_publish=None, reconcile=None))


async def test_shadowed_table_and_replication_bypass_are_refused(schema_engine):
    async with schema_engine.connect() as connection:
        await connection.execute(text("CREATE TEMP TABLE v2_ledger (id text)"))
        with pytest.raises(RuntimeError, match="session bypasses"):
            await schema_catalog.validate_catalog(connection, "v2_0029")
        await connection.rollback()
        await connection.execute(text("SET LOCAL session_replication_role = replica"))
        with pytest.raises(RuntimeError, match="session bypasses"):
            await schema_catalog.validate_catalog(connection, "v2_0029")


@pytest.mark.parametrize("change,message", [
    ({"sha256": "0" * 64}, "corrupt"),
    ({"schema_version": "v2_0018"}, "incompatible"),
    ({"verified_postgresql_majors": [99]}, "major version"),
])
async def test_manifest_mismatch_requires_review(schema_engine, monkeypatch, tmp_path, change, message):
    manifest = json.loads(schema_catalog.MANIFEST_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest | change), encoding="utf-8")
    monkeypatch.setattr(schema_catalog, "MANIFEST_PATH", path)
    with pytest.raises(RuntimeError, match=message):
        await v2_schema.require_v2_schema(settings=settings(), engine=schema_engine)


async def test_export_candidate_matches_fresh_manifest_and_never_overwrites(schema_engine, tmp_path):
    from app.db.schema_manifest import export_candidate
    url = schema_engine.url.render_as_string(hide_password=False)
    output = tmp_path / "candidate.json"
    result = await export_candidate(url, output)
    expected = json.loads(schema_catalog.MANIFEST_PATH.read_text(encoding="utf-8"))
    candidate = json.loads(output.read_text(encoding="utf-8"))
    assert candidate["catalog"] == expected["catalog"] and candidate["sha256"] == expected["sha256"]
    assert candidate["verified_postgresql_majors"][0] in expected["verified_postgresql_majors"]
    assert result["status"] == "candidate_exported_requires_review"
    with pytest.raises(ValueError, match="new_output_path_required"):
        await export_candidate(url, output)
    assert json.loads(output.read_text(encoding="utf-8")) == candidate


async def test_candidate_export_refuses_business_data(schema_engine, tmp_path):
    from app.db.schema_manifest import export_candidate
    async with schema_engine.begin() as connection:
        await connection.execute(text("INSERT INTO audit_counters (key,value,updated_at) VALUES ('sentinel',1,now())"))
    output = tmp_path / "candidate.json"
    with pytest.raises(ValueError, match="empty_schema_database_required"):
        await export_candidate(schema_engine.url.render_as_string(hide_password=False), output)
    assert not output.exists()
    async with schema_engine.connect() as connection:
        assert (await connection.execute(text("SELECT value FROM audit_counters WHERE key='sentinel'"))).scalar_one() == 1


@pytest.mark.parametrize("url", ["postgresql+asyncpg://kig@remote/kig_v2_schema_test",
                                 "postgresql+asyncpg://kig@127.0.0.1/kig_preview"])
async def test_candidate_export_refuses_non_qa_target(tmp_path, url):
    from app.db.schema_manifest import export_candidate
    with pytest.raises(ValueError, match="isolated_schema_database_required"):
        await export_candidate(url, tmp_path / "candidate.json")
