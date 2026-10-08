"""Bounded reference checks; never persist or return private native text."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote

from sqlalchemy import JSON, String, Text, cast, column, func, or_, select, values

from app.db.base import Base
from app.design.models import AdminAction
from app.maintenance import codex_snapshot
from app.maintenance.orphan_assets import CleanupError, MAX_BATCH_BYTES, MAX_FILES, checked, digest, fingerprint, root_path

MAX_CANDIDATES = 500
MAX_RECORD = 64 * 1024 * 1024
MAX_ROWS = 100000


def tokens(items):
    result = {}
    for item in items:
        name = item["key"].rsplit("/", 1)[-1]
        # A delivery key can be stored without the PNG suffix.
        for token in {name.lower(), name[:-4].lower() if name.endswith((".png", ".jpg")) else name.lower()}:
            result.setdefault(token, set()).add(item["key"])
    return result


async def business_references(db, items):
    if not items:
        return set()
    pairs = [(key, token) for token, keys in tokens(items).items() for key in keys]
    wanted = values(column("key", String), column("token", String), name="wanted").data(pairs)
    found = set()
    for table in sorted(Base.metadata.tables.values(), key=lambda value: value.name):
        if table.name == AdminAction.__tablename__:
            continue
        columns = [col for col in table.columns if isinstance(col.type, (String, Text, JSON))]
        if columns:
            condition = or_(*(func.strpos(func.lower(cast(col, Text)), wanted.c.token) > 0 for col in columns))
            found.update(await db.scalars(select(wanted.c.key).select_from(wanted.join(table, condition)).distinct()))
    return found


def native_references(profile, items, version):
    """Inspect known native stores under the held profile lock, including forks.

    All profile histories participate so an unregistered or shared thread still
    protects its referenced file. Only candidate keys and a source digest leave
    this function; credentials/config/log files are neither opened nor indexed.
    """
    if version != codex_snapshot.TESTED_CODEX_VERSION:
        raise CleanupError("unsupported_native_reference_version")
    profile = root_path(profile)
    if len(items) > MAX_CANDIDATES:
        raise CleanupError("intermediate_batch_too_large")
    lookup = tokens(items)
    pattern = re.compile("|".join(re.escape(token) for token in sorted(lookup))) if lookup else None
    matched = set()
    total = rows = nodes = 0
    contents = hashlib.sha256()

    def consume(value, depth=0):
        nonlocal total, nodes
        nodes += 1
        if depth > 64 or nodes > 1000000:
            raise CleanupError("native_reference_inventory_too_large")
        if isinstance(value, bytes):
            try: value = value.decode("utf-8")
            except UnicodeError: raise CleanupError("native_reference_encoding_unknown") from None
        if isinstance(value, str):
            encoded = value.encode("utf-8")
            total += len(encoded)
            if len(encoded) > MAX_RECORD or total > MAX_BATCH_BYTES:
                raise CleanupError("native_reference_inventory_too_large")
            contents.update(len(encoded).to_bytes(8, "big")); contents.update(encoded)
            normalized = value.lower()
            for _ in range(3):
                if pattern:
                    for match in pattern.finditer(normalized):
                        matched.update(lookup[match.group()])
                decoded = unquote(normalized)
                if decoded == normalized: break
                normalized = decoded
            if value.lstrip()[:1] in {'{', '[', '"'}:
                try: parsed = json.loads(value)
                except RecursionError:
                    raise CleanupError("native_reference_inventory_too_large") from None
                except ValueError: return
                if parsed != value: consume(parsed, depth + 1)
        elif isinstance(value, dict):
            for key, child in value.items():
                consume(key, depth + 1); consume(child, depth + 1)
        elif isinstance(value, (list, tuple)):
            for child in value: consume(child, depth + 1)

    paths = {}
    entries = 0
    for directory in ("sessions", "archived_sessions"):
        folder = checked(profile, directory)
        if not folder.exists(): continue
        if not folder.is_dir(): raise CleanupError("unsafe_native_history")
        stack = [folder]
        while stack:
            folder = stack.pop()
            for child in folder.iterdir():
                key = child.relative_to(profile).as_posix()
                child = checked(profile, key)
                entries += 1
                if entries > MAX_FILES: raise CleanupError("native_reference_inventory_too_large")
                if child.is_dir(): stack.append(child)
                elif child.suffix == ".jsonl": paths[key] = child
                else: raise CleanupError("unknown_native_history_file")
    for child in profile.iterdir():
        entries += 1
        if entries > MAX_FILES: raise CleanupError("native_reference_inventory_too_large")
        if child.name.startswith(("state_", "thread_history_")) and child.name.endswith(".sqlite") and child.name not in codex_snapshot.DATABASES:
            raise CleanupError("unsupported_native_reference_database")
    for name in ("history.jsonl", "session_index.jsonl"):
        path = checked(profile, name)
        if path.exists(): paths[name] = path
    database_paths = {name: checked(profile, name) for name in codex_snapshot.DATABASES}
    existing = [path.exists() for path in database_paths.values()]
    if any(existing) != all(existing) or (paths and not all(existing)):
        raise CleanupError("native_reference_database_missing")
    source_files = dict(paths)
    for name, path in database_paths.items():
        if not path.exists(): continue
        source_files[name] = path
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = checked(profile, name + suffix)
            if sidecar.exists():
                if suffix == "-journal": raise CleanupError("native_reference_journal_present")
                if sidecar.stat().st_nlink != 1: raise CleanupError("linked_native_history")
                if suffix == "-wal": source_files[name + suffix] = sidecar
    def snapshot():
        result = {}
        size = 0
        for key, path in sorted(source_files.items()):
            if path.stat().st_nlink != 1: raise CleanupError("linked_native_history")
            value = fingerprint(path); size += value["bytes"]
            if size > MAX_BATCH_BYTES: raise CleanupError("native_reference_inventory_too_large")
            result[key] = value
        return result
    before = snapshot()
    for name, path in database_paths.items():
        if not path.exists(): continue
        with closing(codex_snapshot._connect(path)) as db:
            codex_snapshot._schema(db, codex_snapshot.DATABASES[name])
            if name == "state_5.sqlite":
                for (rollout,) in db.execute("SELECT rollout_path FROM threads"):
                    try: key = Path(rollout).relative_to(profile).as_posix()
                    except (ValueError, TypeError): raise CleanupError("native_rollout_outside_profile") from None
                    if key.split("/")[0] not in {"sessions", "archived_sessions"} or key not in paths: raise CleanupError("native_rollout_missing")
            for table in sorted(codex_snapshot.DATABASES[name] - {"_sqlx_migrations"}):
                for row in db.execute('SELECT * FROM "' + table + '"'):
                    rows += 1
                    if rows > MAX_ROWS: raise CleanupError("native_reference_inventory_too_large")
                    consume(row)
    for _, path in sorted(paths.items()):
        with path.open("rb") as stream:
            while line := stream.readline(MAX_RECORD + 1):
                if len(line) > MAX_RECORD: raise CleanupError("native_reference_inventory_too_large")
                rows += 1
                if rows > MAX_ROWS: raise CleanupError("native_reference_inventory_too_large")
                try: value = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    raise CleanupError("native_reference_history_invalid") from None
                consume(value)
    if before != snapshot(): raise CleanupError("native_reference_history_changed")
    return matched, digest({"sources": before, "content": contents.hexdigest()})
