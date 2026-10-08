"""Release checks must not migrate data, prune native history or update Codex."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import main
from app.codex_profiles import session_cleanup
from app.codex_update import update_codex_cli
from app.core.config import Settings
from app.db import v2_schema
from app.maintenance import v2_preflight
from tests.v2.test_migrations import migration_database, migrate, tables


def settings_for(tmp_path, **overrides):
    assets, runtime = tmp_path / "assets", tmp_path / "runtime"
    assets.mkdir(exist_ok=True)
    runtime.mkdir(exist_ok=True)
    values = dict(v2_enabled=True, v2_runtime_mode="local", v2_local_user_id="owner",
                  v2_codex_path=str(tmp_path / "codex.exe"),
                  v2_storage_dir=str(assets), v2_runtime_dir=str(runtime),
                  codex_update_enabled=False, codex_session_cleanup_enabled=False,
                  database_url="postgresql+asyncpg://kig@127.0.0.1/test")
    return Settings(_env_file=None, **(values | overrides))


@pytest.mark.parametrize("overrides,code", [
    ({"v2_enabled": False}, "v2_disabled"),
    ({"app_env": "production"}, "local_trial_only"),
    ({"v2_runtime_mode": "disabled"}, "local_trial_only"),
    ({"v2_local_user_id": ""}, "test_owner_required"),
    ({"v2_review_model": ""}, "independent_review_model_required"),
    ({"v2_review_model": "gpt-6.1-sol"}, "independent_review_model_required"),
    ({"v2_codex_version": "0.153.4"}, "untested_codex_version"),
    ({"v2_codex_path": "codex"}, "absolute_codex_path_required"),
    ({"codex_update_enabled": True}, "legacy_maintenance_must_be_disabled"),
    ({"codex_session_cleanup_enabled": True}, "legacy_maintenance_must_be_disabled"),
    ({"database_url": "sqlite://"}, "postgresql_required"),
    ({"v2_storage_dir": "relative"}, "existing_absolute_data_roots_required"),
])
def test_preflight_rejects_unsafe_configuration(tmp_path, overrides, code):
    with pytest.raises(v2_preflight.PreflightError, match=code):
        v2_preflight.validate_settings(settings_for(tmp_path, **overrides))


def test_preflight_keeps_data_roots_separate(tmp_path):
    settings = settings_for(tmp_path)
    settings.v2_runtime_dir = str(tmp_path)
    with pytest.raises(v2_preflight.PreflightError, match="separate_data_roots_required"):
        v2_preflight.validate_settings(settings)


def test_v2_does_not_update_binary_or_overwrite_old_status(tmp_path):
    status_file = tmp_path / "update.json"
    status_file.write_bytes(b"old-status")
    settings = settings_for(tmp_path, codex_update_enabled=True,
                            codex_update_status_path=str(status_file))
    def unexpected(*args, **kwargs):
        pytest.fail("V2 invoked legacy Codex command")
    status = update_codex_cli(settings, runner=unexpected)
    assert not status.succeeded and status.error == "v2_runtime_pinned"
    assert status.version_before is status.version_after is None
    assert status_file.read_bytes() == b"old-status"


async def test_v2_history_survives_legacy_cleanup_entrypoints(tmp_path, monkeypatch):
    settings = settings_for(tmp_path, codex_session_cleanup_enabled=True,
                            codex_profiles_dir=str(tmp_path / "profiles"))
    history = tmp_path / "history.jsonl"
    history.write_bytes(b"referenced history")
    os.utime(history, (1, 1))
    def unexpected(*args, **kwargs):
        pytest.fail("V2 inspected legacy cleanup roots")
    monkeypatch.setattr(session_cleanup, "_session_roots", unexpected)
    assert session_cleanup.cleanup_codex_sessions(settings).deleted == 0
    async with session_cleanup.session_cleanup_lifespan(settings):
        assert history.read_bytes() == b"referenced history"


async def test_unmigrated_api_refuses_without_creating_any_tables(migration_database, tmp_path, monkeypatch):
    settings = settings_for(tmp_path, database_url=migration_database)
    engine = create_async_engine(migration_database)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(v2_schema, "get_settings", lambda: settings)
    monkeypatch.setattr(v2_schema, "get_engine", lambda: engine)
    try:
        with pytest.raises(Exception):
            async with main._lifespan(None):
                pytest.fail("unmigrated API started")
        assert await tables(migration_database) == set()
    finally:
        await engine.dispose()


async def test_migrated_api_starts_with_database_read_only(migration_database, tmp_path, monkeypatch):
    result = migrate(migration_database)
    assert result.returncode == 0, result.stderr
    before = await tables(migration_database)
    settings = settings_for(tmp_path, database_url=migration_database,
                            codex_session_cleanup_enabled=True)
    engine = create_async_engine(migration_database, connect_args={
        "server_settings": {"default_transaction_read_only": "on"}})
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(v2_schema, "get_settings", lambda: settings)
    monkeypatch.setattr(v2_schema, "get_engine", lambda: engine)
    from sqlalchemy.ext.asyncio import async_sessionmaker
    import app.admin.access_collector as collector_module
    monkeypatch.setattr(collector_module, "get_sessionmaker", lambda: async_sessionmaker(engine))
    app=main.create_app()
    try:
        async with main._lifespan(app):
            async with engine.connect() as connection:
                assert (await connection.execute(text("SHOW transaction_read_only"))).scalar_one() == "on"
                assert (await connection.execute(text("SELECT count(*) FROM audit_counters"))).scalar_one() == 0
            assert app.state.access_collector.last_success is None
            assert app.state.access_collector.pending_failure is not None
        assert await tables(migration_database) == before
    finally:
        await engine.dispose()


async def test_preflight_failure_hides_runtime_details(tmp_path, monkeypatch):
    class Broken:
        def __init__(self, settings):
            raise ValueError("secret-private-path")
    monkeypatch.setattr(v2_preflight, "LocalRuntime", Broken)
    with pytest.raises(v2_preflight.PreflightError, match="^pinned_runtime_unavailable$"):
        await v2_preflight.inspect_release(settings_for(tmp_path))


def test_cli_hides_invalid_config_values(tmp_path):
    config = tmp_path / ".env"
    config.write_text("V2_ENABLED=secret-private-value\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "V2_ENABLED"}
    result = subprocess.run([sys.executable, "-m", "app.maintenance.v2_preflight",
                             "--env-file", str(config)], env=env, capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"status": "refused", "code": "preflight_unavailable"}
    assert not result.stderr and "secret-private-value" not in result.stdout


@pytest.mark.parametrize("enabled,version,environment,allowed", [
    (False,"0.159.0-alpha.12.1","test",False),
    (True,"0.159.0-alpha.12.1","test",True),
    (True,"0.159.0-alpha.12.1","development",True),
    (True,"0.159.0-alpha.12.1","production",False),
    (True,"0.159.2","test",False),
])
def test_local_candidate_requires_explicit_exact_version_and_local_environment(tmp_path,enabled,version,environment,allowed):
    settings=settings_for(tmp_path,v2_local_trial_candidate_enabled=enabled,
                          v2_codex_version=version,app_env=environment)
    if allowed:
        v2_preflight.validate_settings(settings)
    else:
        with pytest.raises(v2_preflight.PreflightError):
            v2_preflight.validate_settings(settings)
