import argparse
import asyncio
import shutil
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.paths import repo_root, resolve_repo_path
from app.core.time import utc_now
from app.db.models import AuditCounter
from app.db.session import ensure_database_schema, get_sessionmaker


IMAGE_DIRECTORY_SETTINGS = (
    ("codex_output_dir", "generated"),
    ("reference_upload_dir", "references"),
    ("mock_output_dir", "mock-outputs"),
)


def _sqlite_path() -> Path:
    return resolve_repo_path(get_settings().generation_audit_db_path)


def _runtime_root() -> Path:
    return (repo_root() / "runtime").resolve()


def _legacy_audit_db_path() -> Path:
    return (_runtime_root() / "generation_audit.sqlite3").resolve()


def _empty_legacy_counters() -> dict[str, int]:
    return {
        "legacy_total_users": 0,
        "legacy_total_calls": 0,
        "legacy_generated_images": 0,
    }


def _read_legacy_counters(path: Path) -> dict[str, int]:
    if not path.is_file():
        return _empty_legacy_counters()

    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        jobs = connection.execute(
            "SELECT character_session_id, status, generation_mode FROM generation_jobs"
        ).fetchall()
        outputs = connection.execute(
            "SELECT COUNT(*) AS count FROM generation_outputs"
        ).fetchone()["count"]

    statuses = Counter(str(row["status"]) for row in jobs)
    modes = Counter(str(row["generation_mode"]) for row in jobs)
    counters = {
        "legacy_total_users": len(
            {str(row["character_session_id"]) for row in jobs if row["character_session_id"]}
        ),
        "legacy_total_calls": len(jobs),
        "legacy_generated_images": int(outputs),
    }
    counters.update({f"legacy_status_{key}": value for key, value in statuses.items()})
    counters.update({f"legacy_mode_{key}": value for key, value in modes.items()})
    return counters


async def _store_counters(counters: dict[str, int]) -> None:
    now = utc_now()
    async with get_sessionmaker()() as session:
        for key, value in counters.items():
            counter = await session.get(AuditCounter, key)
            if counter is None:
                session.add(AuditCounter(key=key, value=value, updated_at=now))
            else:
                counter.value = value
                counter.updated_at = now
        await session.commit()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_safe_image_directory(path: Path, expected_name: str) -> bool:
    resolved = path.resolve()
    runtime_root = _runtime_root()
    if path.is_symlink() or resolved.is_symlink():
        return False
    if resolved.name != expected_name:
        return False
    return resolved != runtime_root and _is_relative_to(resolved, runtime_root)


def _safe_legacy_sqlite_path() -> tuple[Path | None, Path]:
    configured = _sqlite_path()
    resolved = configured.resolve()
    if configured.is_symlink() or resolved.is_symlink():
        return None, resolved
    if resolved != _legacy_audit_db_path():
        return None, resolved
    return resolved, resolved


def _delete_image_directories() -> tuple[list[str], list[str]]:
    settings = get_settings()
    deleted_paths: list[str] = []
    skipped_paths: list[str] = []

    for setting_name, expected_name in IMAGE_DIRECTORY_SETTINGS:
        configured_path = getattr(settings, setting_name)
        path = resolve_repo_path(configured_path)
        resolved = path.resolve()
        if not path.exists() and not path.is_symlink():
            continue
        if not _is_safe_image_directory(path, expected_name):
            skipped_paths.append(str(resolved))
            continue
        if not resolved.is_dir():
            skipped_paths.append(str(resolved))
            continue
        shutil.rmtree(resolved)
        deleted_paths.append(str(resolved))

    return deleted_paths, skipped_paths


async def run_destructive_account_migration(
    preserve_audit_counters: bool,
    delete_images: bool,
) -> dict[str, Any]:
    await ensure_database_schema()
    sqlite_path, configured_sqlite_path = _safe_legacy_sqlite_path()
    skipped_paths: list[str] = []
    if sqlite_path is None and (
        configured_sqlite_path.exists() or configured_sqlite_path.is_symlink()
    ):
        skipped_paths.append(str(configured_sqlite_path))

    counters = (
        _read_legacy_counters(sqlite_path)
        if preserve_audit_counters and sqlite_path is not None
        else {}
    )
    if counters:
        await _store_counters(counters)

    if sqlite_path is not None and sqlite_path.exists():
        sqlite_path.unlink()

    deleted_paths: list[str] = []
    if delete_images:
        image_deleted_paths, image_skipped_paths = _delete_image_directories()
        deleted_paths.extend(image_deleted_paths)
        skipped_paths.extend(image_skipped_paths)

    return {**counters, "deleted_paths": deleted_paths, "skipped_paths": skipped_paths}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Destructively migrate legacy anonymous generation state."
    )
    parser.add_argument("--preserve-audit-counters", action="store_true")
    parser.add_argument("--delete-images", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        run_destructive_account_migration(
            preserve_audit_counters=args.preserve_audit_counters,
            delete_images=args.delete_images,
        )
    )
    print(result)


if __name__ == "__main__":
    main()
