"""Selected native history deletion under an already-held, quiescent profile lease.

The two SQLite commits and file changes are deliberately resumable, not claimed
to be one atomic transaction. Receipts contain hashes/counts, never history text.
"""
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import unquote

from app.maintenance import codex_snapshot as snapshot
from app.maintenance.orphan_assets import CleanupError, MAX_BATCH_BYTES, MAX_FILES, checked, digest, fingerprint, root_path
from app.maintenance.runtime_references import MAX_RECORD, MAX_ROWS
from app.maintenance.runtime_retention import identity

TABLE_KEYS = {
    "threads": "id", "thread_turns": "thread_id", "thread_items": "thread_id",
    "thread_history_projection_state": "thread_id", "thread_realtime_items": "thread_id",
    "thread_dynamic_tools": "thread_id", "thread_attachments": "thread_id",
}
INDEX_KEYS = {"history.jsonl": "session_id", "session_index.jsonl": "id"}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        default=lambda item: {"bytes": item.hex()} if isinstance(item, bytes) else str(item))


def row_proof(rows):
    return {"count": len(rows), "sha256": hashlib.sha256(encoded(rows).encode()).hexdigest()}


def file_proof(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def mentions(value, ids, depth=0):
    if depth > 64: raise CleanupError("native_history_too_deep")
    if isinstance(value, bytes):
        try: value = value.decode("utf-8")
        except UnicodeError: raise CleanupError("native_history_encoding_unknown") from None
    if isinstance(value, str):
        lower = value.lower()
        for _ in range(3):
            if any(key in lower for key in ids): return True
            decoded = unquote(lower)
            if decoded == lower: break
            lower = decoded
        if value.lstrip()[:1] in {'{', '[', '"'}:
            try: parsed = json.loads(value)
            except RecursionError: raise CleanupError("native_history_too_deep") from None
            except ValueError: return False
            if parsed != value: return mentions(parsed, ids, depth + 1)
        return False
    if isinstance(value, dict):
        return any(mentions((key, item), ids, depth + 1) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return any(mentions(item, ids, depth + 1) for item in value)
    return False


def selected(table, record, ids):
    if table == "thread_spawn_edges":
        return record["parent_thread_id"] in ids and record["child_thread_id"] in ids
    return table in TABLE_KEYS and record[TABLE_KEYS[table]] in ids


def paths(profile):
    result = {}
    stack = []
    for directory in ("sessions", "archived_sessions"):
        path = checked(profile, directory)
        if path.exists():
            if not path.is_dir(): raise CleanupError("unsafe_native_directory")
            stack.append(path)
    count = 0
    while stack:
        for child in stack.pop().iterdir():
            count += 1
            if count > MAX_FILES: raise CleanupError("native_history_too_large")
            key = child.relative_to(profile).as_posix()
            child = checked(profile, key)
            if child.is_dir(): stack.append(child)
            elif child.suffix == ".jsonl": result[key] = child
            else: raise CleanupError("unknown_native_history_file")
    for name in INDEX_KEYS:
        path = checked(profile, name)
        if path.exists(): result[name] = path
    for child in profile.iterdir():
        if child.name.startswith(("state_", "thread_history_")) and child.name.endswith(".sqlite") and child.name not in snapshot.DATABASES:
            raise CleanupError("unsupported_native_database")
    return result


def regular(path):
    if path.stat().st_nlink != 1: raise CleanupError("linked_native_history")
    return fingerprint(path)


def inspect_store(profile, ids, version, *, previous=None, connections=None):
    """Preflight the entire batch. Partial progress is allowed only by its receipt."""
    if version != snapshot.TESTED_CODEX_VERSION: raise CleanupError("unsupported_native_version")
    if not ids or len(ids) > 100 or len(set(ids)) != len(ids) or not all(identity(key) for key in ids):
        raise CleanupError("invalid_native_selection")
    profile = root_path(profile)
    ids = set(ids)
    inventory = paths(profile)
    total = row_count = 0
    proofs, rollouts = {}, {}
    with ExitStack() as stack:
        stores = connections or {name: stack.enter_context(closing(snapshot._connect(checked(profile, name))))
                                 for name in snapshot.DATABASES}
        # File-path references also protect histories whose rollout basename omits the UUID.
        marks = ",".join("?" for _ in ids)
        targets = set(ids)
        candidate_paths = [row[0] for row in stores["state_5.sqlite"].execute(
            f"SELECT rollout_path FROM threads WHERE id IN ({marks})", sorted(ids))]
        if previous:
            candidate_paths.extend(str(profile/key) for key in previous["rollouts"])
        for path in candidate_paths:
            try: relative = Path(path).relative_to(profile).as_posix()
            except (TypeError, ValueError): raise CleanupError("native_rollout_outside_profile") from None
            targets.update((str(path).lower(), relative.lower(), Path(path).name.lower()))
        for name, db in stores.items():
            regular(checked(profile, name))
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = checked(profile, name + suffix)
                if sidecar.exists():
                    if suffix == "-journal": raise CleanupError("native_journal_present")
                    regular(sidecar)
            snapshot._schema(db, snapshot.DATABASES[name])
            owned = {}
            for table in sorted(snapshot.DATABASES[name] - {"_sqlx_migrations"}):
                columns = [row[1] for row in db.execute('PRAGMA table_info("' + table + '")')]
                chosen = []
                for row in db.execute('SELECT * FROM "' + table + '" ORDER BY rowid'):
                    row_count += 1
                    total += len(encoded(row))
                    if row_count > MAX_ROWS or total > MAX_BATCH_BYTES: raise CleanupError("native_history_too_large")
                    record = dict(zip(columns, row))
                    if selected(table, record, ids):
                        if table in {"thread_dynamic_tools", "thread_attachments"}:
                            raise CleanupError("native_external_dependencies")
                        if table == "threads":
                            if record.get("project_id") or record.get("thread_section_id"):
                                raise CleanupError("native_external_dependencies")
                            try: key = Path(record["rollout_path"]).relative_to(profile).as_posix()
                            except (TypeError, ValueError): raise CleanupError("native_rollout_outside_profile") from None
                            if key.split("/")[0] not in {"sessions", "archived_sessions"} or not key.endswith(".jsonl"):
                                raise CleanupError("invalid_native_rollout")
                            if key in rollouts: raise CleanupError("shared_native_rollout")
                            rollouts[key] = record["id"]
                        chosen.append(row)
                    elif mentions(row, targets):
                        raise CleanupError("native_history_referenced")
                if table in TABLE_KEYS or table == "thread_spawn_edges": owned[table] = row_proof(chosen)
            proofs[name] = owned
            if previous is not None:
                # Every store is deleted in one transaction. No mixed subset is accepted.
                if owned != previous["stores"][name] and any(item["count"] for item in owned.values()):
                    raise CleanupError("native_history_changed")
        if previous is None:
            if set(rollouts.values()) != ids: raise CleanupError("native_thread_missing")
        else:
            expected_rollouts = previous["rollouts"]
            if any(key not in expected_rollouts or expected_rollouts[key]["thread_id"] != owner for key, owner in rollouts.items()):
                raise CleanupError("native_rollout_changed")
            rollouts = {key: item["thread_id"] for key, item in expected_rollouts.items()}
        result_files, indexes, replacements = {}, {}, {}
        for key, path in sorted(inventory.items()):
            before = regular(path)
            total += before["bytes"]
            if total > MAX_BATCH_BYTES: raise CleanupError("native_history_too_large")
            retained, first, count = [], None, 0
            with path.open("rb") as stream:
                while line := stream.readline(MAX_RECORD + 1):
                    row_count += 1
                    if len(line) > MAX_RECORD or row_count > MAX_ROWS: raise CleanupError("native_history_too_large")
                    try: value = json.loads(line)
                    except (ValueError, UnicodeError, RecursionError): raise CleanupError("invalid_native_history") from None
                    if first is None: first = value
                    if key in rollouts:
                        continue
                    if key in INDEX_KEYS and isinstance(value, dict) and value.get(INDEX_KEYS[key]) in ids:
                        count += 1
                    else:
                        if mentions(value, targets): raise CleanupError("native_history_referenced")
                        retained.append(line)
            if regular(path) != before: raise CleanupError("native_history_changed")
            if key in rollouts:
                if not isinstance(first, dict) or first.get("type") != "session_meta" or first.get("payload", {}).get("id") != rollouts[key]:
                    raise CleanupError("native_rollout_identity_mismatch")
                result_files[key] = {"thread_id": rollouts[key], **before}
                if previous and result_files[key] != previous["rollouts"][key]: raise CleanupError("native_history_changed")
            elif key in INDEX_KEYS:
                new = b"".join(retained)
                indexes[key] = {"before": before, "after": file_proof(new)}
                replacements[key] = new
                if previous:
                    old = previous["indexes"].get(key)
                    if old is None or before not in (old["before"], old["after"]) or file_proof(new) != old["after"]:
                        raise CleanupError("native_index_changed")
        if previous:
            if set(indexes) != set(previous["indexes"]): raise CleanupError("native_index_changed")
            # Missing rollout is allowed only after all native database stores have been removed.
            if set(rollouts) - set(result_files) and any(item["count"] for store in proofs.values() for item in store.values()):
                raise CleanupError("native_rollout_missing")
        elif set(result_files) != set(rollouts): raise CleanupError("native_rollout_missing")
        return {"stores": proofs, "rollouts": result_files, "indexes": indexes}, replacements


def delete_rows(db, name, ids):
    marks = ",".join("?" for _ in ids)
    # Child tables first, then state threads. Preserve migrations and unrelated records.
    for table in ("thread_realtime_items", "thread_items", "thread_turns", "thread_history_projection_state",
                  "thread_dynamic_tools", "thread_attachments", "thread_spawn_edges", "threads"):
        if table not in snapshot.DATABASES[name]: continue
        if table == "thread_spawn_edges":
            condition = f"parent_thread_id IN ({marks}) AND child_thread_id IN ({marks})"
            args = (*ids, *ids)
        else:
            condition, args = f"{TABLE_KEYS[table]} IN ({marks})", ids
        db.execute(f'DELETE FROM "{table}" WHERE {condition}', args)


def replace_index(path, data):
    # Private same-directory temporary file, atomic replace; no unselected line is reformatted.
    descriptor, name = tempfile.mkstemp(prefix=".kig-history-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def purge_store(profile, ids, version, previous):
    profile = root_path(profile)
    with ExitStack() as stack:
        stores = {}
        for name in snapshot.DATABASES:
            path = checked(profile, name)
            regular(path)
            db = stack.enter_context(closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)))
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            stores[name] = db
        _, replacements = inspect_store(profile, ids, version, previous=previous, connections=stores)
        for name, db in stores.items():
            delete_rows(db, name, ids)
            if db.execute("PRAGMA foreign_key_check").fetchone(): raise CleanupError("native_foreign_key_invalid")
            db.commit()
        # Commits may leave historical WAL bytes; checkpoint before deleting rollouts.
        for db in stores.values():
            if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                raise CleanupError("native_checkpoint_busy")
        for key, data in replacements.items():
            path = checked(profile, key)
            if regular(path) not in (previous["indexes"][key]["before"], previous["indexes"][key]["after"]):
                raise CleanupError("native_index_changed")
            if regular(path) != file_proof(data): replace_index(path, data)
        for key, info in previous["rollouts"].items():
            path = checked(profile, key)
            if path.exists():
                if regular(path) != {k: info[k] for k in ("bytes", "sha256")}: raise CleanupError("native_history_changed")
                path.unlink()
        after, _ = inspect_store(profile, ids, version, previous=previous)
        if after["rollouts"] or any(item["count"] for store in after["stores"].values() for item in store.values()):
            raise CleanupError("native_purge_incomplete")
