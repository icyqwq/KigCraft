"""Version-pinned, credential-free snapshots of explicitly owned local Codex history.

This is a read-only recovery artifact, not authorization to resume a restored thread.
Never copies the host profile, login material, unrelated threads, or tool configuration.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path, PurePosixPath
from uuid import UUID

from app.codex_runtime.launch import TESTED_CODEX_VERSION

STATE_TABLES = {
    "_sqlx_migrations", "threads", "thread_dynamic_tools", "backfill_state",
    "thread_spawn_edges", "remote_control_enrollments", "external_agent_config_imports",
    "thread_sections", "rollout_migration_state", "rollout_migration_skipped_rollouts",
    "projects", "project_roots", "project_idempotency_keys", "thread_attachments",
}
HISTORY_TABLES = {
    "_sqlx_migrations", "thread_turns", "thread_items",
    "thread_history_projection_state", "thread_realtime_items",
}
DATABASES = {"state_5.sqlite": STATE_TABLES, "thread_history_1.sqlite": HISTORY_TABLES}


class SnapshotError(ValueError):
    pass


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_path(root: Path, relative: str) -> Path:
    """Only portable, contained regular files; reject Windows aliases and reparse paths."""
    parts = PurePosixPath(relative).parts
    if (not relative or "\\" in relative or relative.startswith("/") or
            any(part in {"", ".", ".."} or ":" in part or part.endswith((" ", ".")) for part in parts)
            or str(PurePosixPath(relative)) != relative):
        raise SnapshotError("invalid_snapshot_path")
    current = root
    for part in parts:
        current /= part
        if current.is_symlink() or current.is_junction():
            raise SnapshotError("linked_snapshot_path")
    if not current.resolve().is_relative_to(root.resolve()) or not current.is_file():
        raise SnapshotError("missing_snapshot_file")
    return current


def _connect(path: Path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def _schema(db, expected):
    rows = db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
    tables = {name for kind, name, _ in rows if kind == "table" and not name.startswith("sqlite_")}
    known_triggers = ({"threads_created_at_ms_after_insert", "threads_updated_at_ms_after_insert",
        "threads_created_at_ms_after_update", "threads_updated_at_ms_after_update", "threads_recency_at_after_insert"}
        if expected == STATE_TABLES else {"thread_realtime_items_projection_cleanup"})
    if (tables != expected or any(kind not in {"table", "index", "trigger"} for kind, _, _ in rows)
            or {name for kind, name, _ in rows if kind == "trigger"} != known_triggers):
        raise SnapshotError("unsupported_codex_schema")
    selected = [(kind, name, sql) for kind, name, sql in rows if not name.startswith("sqlite_")]
    schemas = json.loads(Path(__file__).with_name("codex_snapshot_schema.json").read_text(encoding="utf-8"))
    name = "state_5.sqlite" if expected == STATE_TABLES else "thread_history_1.sqlite"
    if [list(row) for row in selected] != schemas[name]:
        raise SnapshotError("unsupported_codex_schema")
    return selected


def _selected_rows(db, table, ids):
    marks = ",".join("?" for _ in ids)
    if table == "_sqlx_migrations":
        return db.execute('SELECT * FROM "_sqlx_migrations" ORDER BY version').fetchall()
    if table == "threads":
        rows = db.execute(f'SELECT * FROM threads WHERE id IN ({marks}) ORDER BY id', ids).fetchall()
        columns = [row[1] for row in db.execute("PRAGMA table_info(threads)")]
        if len(rows) != len(ids):
            raise SnapshotError("missing_owned_thread")
        for row in rows:
            record = dict(zip(columns, row))
            if record.get("project_id") or record.get("thread_section_id"):
                raise SnapshotError("thread_has_external_dependencies")
        return rows
    if table in {"thread_turns", "thread_items", "thread_history_projection_state",
                 "thread_realtime_items", "thread_dynamic_tools", "thread_attachments"}:
        rows = db.execute(f'SELECT * FROM "{table}" WHERE thread_id IN ({marks}) ORDER BY rowid', ids).fetchall()
        if table in {"thread_dynamic_tools", "thread_attachments"} and rows:
            raise SnapshotError("thread_has_external_dependencies")
        return rows
    if table == "thread_spawn_edges":
        rows = db.execute(f'SELECT * FROM thread_spawn_edges WHERE parent_thread_id IN ({marks}) OR child_thread_id IN ({marks}) ORDER BY child_thread_id', (*ids, *ids)).fetchall()
        if any(row[0] not in ids or row[1] not in ids for row in rows):
            raise SnapshotError("thread_has_external_dependencies")
        return rows
    # Host enrollment, projects, import records, and housekeeping are not application state.
    return []


def _capture(db, name, ids):
    schema = _schema(db, DATABASES[name])
    rows = {table: _selected_rows(db, table, ids) for kind, table, _ in schema if kind == "table"}
    return schema, rows


def create_snapshot(source_home: Path, destination: Path, thread_ids: list[str], *, version: str):
    if version != TESTED_CODEX_VERSION:
        raise SnapshotError("unsupported_codex_version")
    ids = sorted(set(thread_ids))
    if not ids or len(ids) != len(thread_ids) or len(ids) > 1000:
        raise SnapshotError("invalid_thread_selection")
    try:
        if any(str(UUID(identity)) != identity for identity in ids):
            raise ValueError()
    except ValueError:
        raise SnapshotError("invalid_thread_selection") from None
    source_home = source_home.resolve()
    if source_home.is_symlink() or destination.exists() or destination.resolve().is_relative_to(source_home):
        raise SnapshotError("unsafe_snapshot_destination")
    # All native reads use transaction snapshots, never SQLite backup() of a personal profile.
    with ExitStack() as stack:
        sources = {name: stack.enter_context(closing(_connect(checked_path(source_home, name)))) for name in DATABASES}
        captured = {name: _capture(db, name, ids) for name, db in sources.items()}
        thread_columns = [r[1] for r in sources["state_5.sqlite"].execute("PRAGMA table_info(threads)")]
        threads = [dict(zip(thread_columns, row)) for row in captured["state_5.sqlite"][1]["threads"]]
        rollouts = {}
        for row in threads:
            original = Path(row["rollout_path"])
            try:
                relative = original.relative_to(source_home).as_posix()
            except ValueError:
                raise SnapshotError("rollout_outside_profile") from None
            source = checked_path(source_home, relative)
            if PurePosixPath(relative).parts[0] not in {"sessions", "archived_sessions"} or source.suffix != ".jsonl":
                raise SnapshotError("invalid_rollout_path")
            with source.open(encoding="utf-8") as stream:
                metadata = json.loads(stream.readline())
            if metadata.get("type") != "session_meta" or metadata.get("payload", {}).get("id") != row["id"]:
                raise SnapshotError("rollout_identity_mismatch")
            rollouts[row["id"]] = {"path": relative, "sha256": digest(source)}
        destination.mkdir(parents=True)
        (destination / "INCOMPLETE").write_text("Snapshot not verified", encoding="utf-8")
        files = {}
        for row in rollouts.values():
            relative = row["path"]
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checked_path(source_home, relative), target)
            if digest(target) != row["sha256"] or digest(checked_path(source_home, relative)) != row["sha256"]:
                raise SnapshotError("native_history_changed")
            files[relative] = {"sha256": row["sha256"], "bytes": target.stat().st_size}
        for name, (schema, rows) in captured.items():
            target = destination / name
            with sqlite3.connect(target) as db:
                for kind in ("table", "index"):
                    for actual, _, sql in schema:
                        if actual == kind:
                            db.execute(sql)
                for table, values in rows.items():
                    if values:
                        marks = ",".join("?" for _ in values[0])
                        db.executemany(f'INSERT INTO "{table}" VALUES ({marks})', values)
                for kind, _, sql in schema:
                    if kind == "trigger":
                        db.execute(sql)
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise SnapshotError("native_database_invalid")
            db.close()
            files[name] = {"sha256": digest(target), "bytes": target.stat().st_size}
        # Reopen each source after copying. A changed selected history invalidates the artifact.
        for name in DATABASES:
            with closing(_connect(checked_path(source_home, name))) as current:
                if _capture(current, name, ids) != captured[name]:
                    raise SnapshotError("native_history_changed")
        for row in rollouts.values():
            if digest(checked_path(source_home, row["path"])) != row["sha256"]:
                raise SnapshotError("native_history_changed")
        manifest = {"format": 1, "scope": "selected_read_only_history", "codex_version": version,
                    "threads": rollouts, "files": files, "credentials_included": False}
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (destination / "INCOMPLETE").unlink()
        return manifest


def verify_snapshot(source: Path, *, version: str):
    if (source / "INCOMPLETE").exists():
        raise SnapshotError("incomplete_snapshot")
    manifest = json.loads(checked_path(source, "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("format") != 1 or manifest.get("scope") != "selected_read_only_history"
            or manifest.get("codex_version") != version or version != TESTED_CODEX_VERSION
            or manifest.get("credentials_included") is not False):
        raise SnapshotError("unsupported_snapshot")
    files = manifest.get("files", {})
    threads = manifest.get("threads", {})
    expected = set(DATABASES) | {row["path"] for row in threads.values()}
    if not threads or set(files) != expected:
        raise SnapshotError("incomplete_snapshot")
    # Reject extra files, including credentials and configuration.
    actual = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()}
    if actual != expected | {"manifest.json"}:
        raise SnapshotError("unexpected_snapshot_files")
    for relative, info in files.items():
        if relative not in DATABASES and (not relative.startswith(("sessions/", "archived_sessions/")) or not relative.endswith(".jsonl")):
            raise SnapshotError("invalid_snapshot_path")
        path = checked_path(source, relative)
        if path.stat().st_size != info["bytes"] or digest(path) != info["sha256"]:
            raise SnapshotError("snapshot_hash_mismatch")
    ids = sorted(threads)
    try:
        if any(str(UUID(identity)) != identity for identity in ids):
            raise ValueError()
    except ValueError:
        raise SnapshotError("invalid_thread_selection") from None
    for name in DATABASES:
        with closing(_connect(checked_path(source, name))) as db:
            _, selected = _capture(db, name, ids)
            for table, rows in selected.items():
                if db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] != len(rows):
                    raise SnapshotError("unrelated_native_data")
    for identity, row in threads.items():
        with checked_path(source, row["path"]).open(encoding="utf-8") as stream:
            meta = json.loads(stream.readline())
        if meta.get("type") != "session_meta" or meta.get("payload", {}).get("id") != identity:
            raise SnapshotError("rollout_identity_mismatch")
    return manifest


def restore_snapshot(source: Path, destination: Path, *, version: str):
    manifest = verify_snapshot(source, version=version)
    if destination.exists() or destination.resolve().is_relative_to(source.resolve()):
        raise SnapshotError("unsafe_restore_destination")
    destination.mkdir(parents=True)
    (destination / "INCOMPLETE").write_text("Read-only restore not verified", encoding="utf-8")
    for relative in manifest["files"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked_path(source, relative), target)
        if digest(target) != manifest["files"][relative]["sha256"]:
            raise SnapshotError("restore_hash_mismatch")
    # Only the independent restored index changes, never the source snapshot or app epoch.
    with sqlite3.connect(destination / "state_5.sqlite") as db:
        for identity, row in manifest["threads"].items():
            db.execute("UPDATE threads SET rollout_path=? WHERE id=?",
                       (str((destination / row["path"]).resolve()), identity))
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SnapshotError("native_database_invalid")
    db.close()
    (destination / "INCOMPLETE").unlink()
    return manifest


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "verify", "restore"])
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--thread-ids", type=Path, help="UTF-8 JSON array of explicitly owned thread IDs")
    args = parser.parse_args()
    try:
        if args.action == "create":
            if args.destination is None or args.thread_ids is None:
                parser.error("create requires --destination and --thread-ids")
            ids = json.loads(args.thread_ids.read_text(encoding="utf-8"))
            result = create_snapshot(args.source, args.destination, ids, version=args.version)
        elif args.action == "restore":
            if args.destination is None:
                parser.error("restore requires --destination")
            result = restore_snapshot(args.source, args.destination, version=args.version)
        else:
            result = verify_snapshot(args.source, version=args.version)
        print(json.dumps({"status": "verified", "threads": len(result["threads"]),
                          "files": len(result["files"]), "codex_version": result["codex_version"]}))
    except (SnapshotError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        # Do not echo native payloads or filesystem/authentication details.
        code = str(exc) if isinstance(exc, SnapshotError) else "snapshot_unavailable"
        parser.exit(1, json.dumps({"status": "refused", "code": code}) + "\n")


if __name__ == "__main__":
    main()
