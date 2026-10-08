import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.codex_profiles import session_cleanup as cleanup
from app.core.config import Settings

NOW = 2_000_000_000
DAY = 86400


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "_open_session_files", lambda: set())
    return SimpleNamespace(
        v2_enabled=False,
        codex_profiles_dir=str(tmp_path / "runtime/codex-profiles"),
        codex_session_cleanup_enabled=True,
        codex_session_retention_days=7,
        codex_session_cleanup_interval_seconds=0.01,
    )


def write(path, age_days=8, content=b"session"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (NOW - age_days * DAY, NOW - age_days * DAY))
    return path


def profile_home(settings, name="account-a"):
    home = Path(settings.codex_profiles_dir) / "profiles" / name
    write(home / "profile.json", content=b"{}")
    return home


def test_deletes_only_expired_rollouts_and_preserves_images_and_credentials(settings):
    home = profile_home(settings)
    old = write(home / "sessions/2026/01/01/rollout-old.jsonl", content=b"old session")
    preserved = [
        write(home / "sessions/2026/01/01/rollout-recent.jsonl", age_days=1),
        write(home / "sessions/rollout-boundary.jsonl", age_days=7),
        write(home / "sessions/notes.jsonl"),
        write(home / "sessions/rollout-image.png"),
        write(home / "generated_images/rollout-old.jsonl"),
        write(home / "generated_images/image.png"),
        write(home / "auth.json"),
        write(home / "config.toml"),
        write(home / "state.sqlite"),
    ]
    original = {path: path.read_bytes() for path in preserved}
    result = cleanup.cleanup_codex_sessions(settings, now=NOW)
    assert result.deleted == 1 and result.deleted_bytes == len(b"old session")
    assert not old.exists()
    assert all(path.read_bytes() == data for path, data in original.items())
    assert result.skipped_recent == 2


def test_cleans_project_default_home_and_all_managed_profiles(settings, monkeypatch, tmp_path):
    root = Path(settings.codex_profiles_dir).parent
    default = write(root / "codex-home/sessions/rollout-default.jsonl")
    a = write(profile_home(settings, "account-a") / "sessions/rollout-a.jsonl")
    b = write(profile_home(settings, "account-b") / "sessions/rollout-b.jsonl")
    personal = write(tmp_path / "personal-home/sessions/rollout-private.jsonl")
    monkeypatch.setenv("CODEX_HOME", str(personal.parent.parent))
    unmanaged = write(
        Path(settings.codex_profiles_dir) / "profiles/unmanaged/sessions/rollout-other.jsonl"
    )
    result = cleanup.cleanup_codex_sessions(settings, now=NOW)
    assert result.deleted == 3
    assert not any(path.exists() for path in (default, a, b))
    assert personal.exists() and unmanaged.exists()


def test_disabled_cleanup_never_scans_or_deletes(settings, monkeypatch):
    old = write(profile_home(settings) / "sessions/rollout-old.jsonl")
    settings.codex_session_cleanup_enabled = False
    monkeypatch.setattr(
        cleanup, "_session_roots", lambda *args: pytest.fail("disabled cleanup scanned")
    )
    assert cleanup.cleanup_codex_sessions(settings, now=NOW).deleted == 0
    assert old.exists()


def test_open_session_is_preserved(settings, monkeypatch):
    old = write(profile_home(settings) / "sessions/rollout-open.jsonl")
    info = old.stat()
    monkeypatch.setattr(cleanup, "_open_session_files", lambda: {(info.st_dev, info.st_ino)})
    result = cleanup.cleanup_codex_sessions(settings, now=NOW)
    assert result.skipped_open == 1 and result.deleted == 0 and old.exists()


def test_recently_modified_during_scan_is_preserved(settings, monkeypatch):
    old = write(profile_home(settings) / "sessions/rollout-changing.jsonl")
    original = Path.lstat
    seen = 0

    def lstat(path):
        nonlocal seen
        if path == old:
            seen += 1
            if seen == 2:
                os.utime(path, (NOW, NOW))
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert cleanup.cleanup_codex_sessions(settings, now=NOW).deleted == 0
    assert old.exists()


def test_symlink_cannot_escape_session_root(settings, tmp_path):
    home = profile_home(settings)
    root = home / "sessions"
    root.mkdir()
    outside = write(tmp_path / "outside/rollout-keep.jsonl")
    try:
        (root / "linked").symlink_to(outside.parent, target_is_directory=True)
        (root / "rollout-link.jsonl").symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit test symlinks")
    assert cleanup.cleanup_codex_sessions(settings, now=NOW).deleted == 0
    assert outside.exists()


def test_permission_error_does_not_stop_other_files(settings, monkeypatch):
    home = profile_home(settings)
    denied = write(home / "sessions/rollout-denied.jsonl")
    allowed = write(home / "sessions/rollout-allowed.jsonl")
    original = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == denied:
            raise PermissionError()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    result = cleanup.cleanup_codex_sessions(settings, now=NOW)
    assert result.errors == 1 and result.deleted == 1
    assert denied.exists() and not allowed.exists()


def test_missing_directories_are_harmless(settings):
    assert cleanup.cleanup_codex_sessions(settings, now=NOW).deleted == 0
    assert not Path(settings.codex_profiles_dir).exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("codex_session_retention_days", 0),
        ("codex_session_retention_days", -1),
        ("codex_session_cleanup_interval_seconds", 0),
    ],
)
def test_invalid_retention_configuration_is_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


async def test_runs_at_startup_periodically_and_stops_on_shutdown(settings, monkeypatch):
    calls = []

    async def run(_settings):
        calls.append(1)

    monkeypatch.setattr(cleanup, "_run_cleanup", run)
    async with cleanup.session_cleanup_lifespan(settings):
        assert len(calls) == 1
        await asyncio.sleep(0.04)
        assert len(calls) >= 2
    count = len(calls)
    await asyncio.sleep(0.03)
    assert len(calls) == count


async def test_sweep_failure_is_contained(settings, monkeypatch):
    def failed(_settings):
        raise PermissionError("private-filename")

    monkeypatch.setattr(cleanup, "cleanup_codex_sessions", failed)
    await cleanup._run_cleanup(settings)


async def test_lifespan_disabled_creates_no_sweeps(settings, monkeypatch):
    settings.codex_session_cleanup_enabled = False

    async def unexpected(_settings):
        pytest.fail("disabled cleanup ran")

    monkeypatch.setattr(cleanup, "_run_cleanup", unexpected)
    async with cleanup.session_cleanup_lifespan(settings):
        pass


@pytest.mark.parametrize("v2_enabled", [False, True])
async def test_app_uses_explicit_v2_schema_or_legacy_startup(settings, monkeypatch, v2_enabled):
    from app import main
    from contextlib import asynccontextmanager

    events = []
    settings.v2_enabled = v2_enabled

    @asynccontextmanager
    async def lifecycle(_settings):
        events.append("cleanup")
        try:
            yield
        finally:
            events.append("closed")

    async def database():
        events.append("database")

    async def v2_schema():
        events.append("v2_schema")

    monkeypatch.setattr("app.db.v2_schema.require_v2_schema", v2_schema)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(cleanup, "session_cleanup_lifespan", lifecycle)
    monkeypatch.setattr("app.db.session.ensure_database_schema", database)
    startup_events = ["v2_schema"] if v2_enabled else ["cleanup", "database"]
    async with main._lifespan(None):
        assert events == startup_events
    expected = startup_events if v2_enabled else [*startup_events, "closed"]
    assert events == expected
