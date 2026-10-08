import sqlite3
from contextlib import closing

from sqlalchemy import delete, select

from app.auth.admin_cli import create_or_update_admin
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.db.models import AuditCounter
from app.db.session import get_sessionmaker
from app.maintenance.destructive_account_migration import run_destructive_account_migration


def _use_safe_runtime_migration_paths(monkeypatch):
    monkeypatch.setenv("GENERATION_AUDIT_DB_PATH", "runtime/generation_audit.sqlite3")
    monkeypatch.setenv("CODEX_OUTPUT_DIR", "runtime/generated")
    monkeypatch.setenv("REFERENCE_UPLOAD_DIR", "runtime/references")
    monkeypatch.setenv("MOCK_OUTPUT_DIR", "runtime/mock-outputs")
    get_settings.cache_clear()


async def test_destructive_migration_preserves_counters_and_deletes_images(test_app, monkeypatch):
    _use_safe_runtime_migration_paths(monkeypatch)
    sqlite_path = resolve_repo_path(get_settings().generation_audit_db_path)
    if sqlite_path.exists():
        sqlite_path.unlink()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(sqlite_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE generation_jobs (
                id TEXT PRIMARY KEY,
                character_session_id TEXT NOT NULL,
                generation_mode TEXT NOT NULL,
                expected_output_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL,
                queue_position INTEGER,
                phase_label TEXT NOT NULL,
                provider TEXT NOT NULL,
                prompt_payload TEXT NOT NULL,
                accepted_output_index INTEGER,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                token_usage TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE generation_outputs (
                job_id TEXT NOT NULL,
                output_index INTEGER NOT NULL,
                object_key TEXT NOT NULL,
                image_url TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                landmarks TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO generation_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "job-1",
                    "session-a",
                    "front_design",
                    1,
                    "succeeded",
                    100,
                    None,
                    "done",
                    "codex",
                    "{}",
                    None,
                    0,
                    None,
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
                (
                    "job-2",
                    "session-b",
                    "turnaround",
                    1,
                    "failed",
                    60,
                    None,
                    "failed",
                    "codex",
                    "{}",
                    None,
                    0,
                    None,
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
            ],
        )
        connection.execute(
            "INSERT INTO generation_outputs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("job-1", 1, "old.webp", "/api/generated/old.webp", 800, 1100, None),
        )
        connection.commit()
    generated = resolve_repo_path(get_settings().codex_output_dir)
    references = resolve_repo_path(get_settings().reference_upload_dir)
    mock_outputs = resolve_repo_path(get_settings().mock_output_dir)
    for path in [generated, references, mock_outputs]:
        path.mkdir(parents=True, exist_ok=True)
        (path / "old.webp").write_bytes(b"old")

    result = await run_destructive_account_migration(
        preserve_audit_counters=True,
        delete_images=True,
    )

    assert result["legacy_total_calls"] == 2
    assert result["legacy_generated_images"] == 1
    assert not sqlite_path.exists()
    assert not generated.exists()
    assert not references.exists()
    assert not mock_outputs.exists()

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AuditCounter))).scalars().all()
    counters = {row.key: row.value for row in rows}
    assert counters["legacy_total_users"] == 2
    assert counters["legacy_status_succeeded"] == 1
    assert counters["legacy_status_failed"] == 1
    assert counters["legacy_mode_front_design"] == 1
    assert counters["legacy_mode_turnaround"] == 1


async def test_admin_summary_includes_destructive_migration_legacy_counters(
    async_client,
    monkeypatch,
):
    _use_safe_runtime_migration_paths(monkeypatch)
    sqlite_path = resolve_repo_path(get_settings().generation_audit_db_path)
    if sqlite_path.exists():
        sqlite_path.unlink()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(sqlite_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE generation_jobs (
                id TEXT PRIMARY KEY,
                character_session_id TEXT NOT NULL,
                generation_mode TEXT NOT NULL,
                expected_output_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL,
                queue_position INTEGER,
                phase_label TEXT NOT NULL,
                provider TEXT NOT NULL,
                prompt_payload TEXT NOT NULL,
                accepted_output_index INTEGER,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                token_usage TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE generation_outputs (
                job_id TEXT NOT NULL,
                output_index INTEGER NOT NULL,
                object_key TEXT NOT NULL,
                image_url TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                landmarks TEXT
            );
            INSERT INTO generation_jobs VALUES
                ('job-legacy-1', 'session-a', 'front_design', 1, 'succeeded', 100, NULL, 'done', 'codex', '{}', NULL, 0, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),
                ('job-legacy-2', 'session-b', 'turnaround', 1, 'failed', 100, NULL, 'failed', 'codex', '{}', NULL, 0, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO generation_outputs VALUES
                ('job-legacy-1', 1, 'old.webp', '/api/generated/old.webp', 800, 1100, NULL),
                ('job-legacy-2', 1, 'old-failed.webp', '/api/generated/old-failed.webp', 800, 1100, NULL);
            """
        )

    generated = resolve_repo_path(get_settings().codex_output_dir)
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "old.webp").write_bytes(b"old")

    await run_destructive_account_migration(
        preserve_audit_counters=True,
        delete_images=True,
    )
    await create_or_update_admin("migration-summary-admin@example.com", "AdminPassphrase7!")
    login_response = await async_client.post(
        "/api/auth/login/password",
        json={
            "email": "migration-summary-admin@example.com",
            "password": "AdminPassphrase7!",
        },
    )
    assert login_response.status_code == 200

    response = await async_client.get("/api/admin/audit/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_calls"] == 2
    assert body["success_rate"] == 0.5
    assert body["failure_rate"] == 0.5
    assert body["image_usage"]["generated_images"] == 2
    assert body["legacy_counters"]["legacy_total_calls"] == 2
    assert body["legacy_counters"]["legacy_status_succeeded"] == 1
    assert body["legacy_counters"]["legacy_status_failed"] == 1
    assert body["legacy_counters"]["legacy_generated_images"] == 2


async def test_destructive_migration_skips_unexpected_image_directory(
    test_app,
    tmp_path,
    monkeypatch,
):
    outside = tmp_path.parent / "outside-generated"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "keep.webp").write_bytes(b"keep")
    monkeypatch.setenv("CODEX_OUTPUT_DIR", str(outside))
    get_settings.cache_clear()

    result = await run_destructive_account_migration(
        preserve_audit_counters=False,
        delete_images=True,
    )

    assert outside.exists()
    assert str(outside.resolve()) in result["skipped_paths"]


async def test_destructive_migration_without_legacy_sqlite_returns_zero_counters(test_app, monkeypatch):
    _use_safe_runtime_migration_paths(monkeypatch)
    sqlite_path = resolve_repo_path(get_settings().generation_audit_db_path)
    if sqlite_path.exists():
        sqlite_path.unlink()

    result = await run_destructive_account_migration(
        preserve_audit_counters=True,
        delete_images=False,
    )

    assert result["legacy_total_users"] == 0
    assert result["legacy_total_calls"] == 0
    assert result["legacy_generated_images"] == 0
    assert result["deleted_paths"] == []
    assert result["skipped_paths"] == []


async def test_destructive_migration_delete_images_false_leaves_image_dirs_intact(test_app):
    generated = resolve_repo_path(get_settings().codex_output_dir)
    references = resolve_repo_path(get_settings().reference_upload_dir)
    mock_outputs = resolve_repo_path(get_settings().mock_output_dir)
    for path in [generated, references, mock_outputs]:
        path.mkdir(parents=True, exist_ok=True)
        (path / "keep.webp").write_bytes(b"keep")

    result = await run_destructive_account_migration(
        preserve_audit_counters=False,
        delete_images=False,
    )

    assert result["deleted_paths"] == []
    assert result["skipped_paths"] == []
    for path in [generated, references, mock_outputs]:
        assert (path / "keep.webp").is_file()


async def test_destructive_migration_without_preserving_counters_deletes_sqlite_without_writing_counters(
    test_app,
    monkeypatch,
):
    _use_safe_runtime_migration_paths(monkeypatch)
    async with get_sessionmaker()() as session:
        await session.execute(delete(AuditCounter))
        await session.commit()

    sqlite_path = resolve_repo_path(get_settings().generation_audit_db_path)
    if sqlite_path.exists():
        sqlite_path.unlink()
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(sqlite_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE generation_jobs (
                id TEXT PRIMARY KEY,
                character_session_id TEXT NOT NULL,
                generation_mode TEXT NOT NULL,
                expected_output_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL,
                queue_position INTEGER,
                phase_label TEXT NOT NULL,
                provider TEXT NOT NULL,
                prompt_payload TEXT NOT NULL,
                accepted_output_index INTEGER,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                token_usage TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE generation_outputs (
                job_id TEXT NOT NULL,
                output_index INTEGER NOT NULL,
                object_key TEXT NOT NULL,
                image_url TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                landmarks TEXT
            );
            INSERT INTO generation_jobs VALUES
                ('job-1', 'session-a', 'front_design', 1, 'succeeded', 100, NULL, 'done', 'codex', '{}', NULL, 0, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO generation_outputs VALUES ('job-1', 1, 'old.webp', '/api/generated/old.webp', 800, 1100, NULL);
            """
        )

    result = await run_destructive_account_migration(
        preserve_audit_counters=False,
        delete_images=False,
    )

    assert not sqlite_path.exists()
    assert "legacy_total_calls" not in result
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(AuditCounter))).scalars().all()
    assert rows == []


async def test_destructive_migration_skips_outside_absolute_image_dirs_with_expected_names(
    test_app,
    tmp_path,
    monkeypatch,
):
    outside_root = tmp_path / "outside"
    generated = outside_root / "generated"
    references = outside_root / "references"
    mock_outputs = outside_root / "mock-outputs"
    for path in [generated, references, mock_outputs]:
        path.mkdir(parents=True, exist_ok=True)
        (path / "keep.webp").write_bytes(b"keep")

    monkeypatch.setenv("CODEX_OUTPUT_DIR", str(generated))
    monkeypatch.setenv("REFERENCE_UPLOAD_DIR", str(references))
    monkeypatch.setenv("MOCK_OUTPUT_DIR", str(mock_outputs))
    get_settings.cache_clear()

    result = await run_destructive_account_migration(
        preserve_audit_counters=False,
        delete_images=True,
    )

    assert result["deleted_paths"] == []
    assert set(result["skipped_paths"]) == {
        str(generated.resolve()),
        str(references.resolve()),
        str(mock_outputs.resolve()),
    }
    for path in [generated, references, mock_outputs]:
        assert (path / "keep.webp").is_file()


async def test_destructive_migration_skips_outside_absolute_legacy_sqlite(
    test_app,
    tmp_path,
    monkeypatch,
):
    outside_sqlite = tmp_path / "generation_audit.sqlite3"
    outside_sqlite.write_bytes(b"not the runtime audit database")
    monkeypatch.setenv("GENERATION_AUDIT_DB_PATH", str(outside_sqlite))
    get_settings.cache_clear()

    result = await run_destructive_account_migration(
        preserve_audit_counters=False,
        delete_images=False,
    )

    assert outside_sqlite.is_file()
    assert str(outside_sqlite.resolve()) in result["skipped_paths"]
