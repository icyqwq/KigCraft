"""Recovery must preserve selected histories without exporting a personal profile."""
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from app.maintenance import codex_snapshot as module
from app.maintenance.codex_snapshot import create_snapshot, restore_snapshot, verify_snapshot, SnapshotError
from app.codex_runtime.launch import TESTED_CODEX_VERSION as VERSION


@pytest.fixture
def profile(tmp_path):
    home = tmp_path / "host"
    home.mkdir()
    schemas = json.loads(Path(module.__file__).with_name("codex_snapshot_schema.json").read_text(encoding="utf-8"))
    for name, schema in schemas.items():
        with sqlite3.connect(home / name) as db:
            for kind in ("table", "index", "trigger"):
                for actual, _, sql in schema:
                    if actual == kind:
                        db.execute(sql)
        db.close()
    ids = [str(uuid4()), str(uuid4())]
    for identity in ids:
        path = home / "sessions" / ("rollout-" + identity + ".jsonl")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": identity}}) + "\n", encoding="utf-8")
        with sqlite3.connect(home / "state_5.sqlite") as db:
            db.execute("""INSERT INTO threads
                (id,rollout_path,created_at,updated_at,source,model_provider,cwd,title,sandbox_policy,approval_mode,model)
                VALUES (?,?,1,1,'cli','openai',?,'private history','read-only','never','gpt-6.1-sol')""",
                (identity, str(path), str(home)))
        db.close()
        with sqlite3.connect(home / "thread_history_1.sqlite") as db:
            db.execute("INSERT INTO thread_turns(thread_id,turn_id,rollout_ordinal,status) VALUES (?, 'turn', 1, 'completed')", (identity,))
            db.execute("""INSERT INTO thread_items(thread_id,turn_id,item_id,rollout_ordinal,created_at_ms,item_json)
                VALUES (?, 'turn', 'user', 1, 1, ?)""", (identity, json.dumps({"text": "private " + identity})))
        db.close()
    (home / "auth.json").write_text("do-not-export-secret", encoding="utf-8")
    (home / "config.toml").write_text("do-not-export-config", encoding="utf-8")
    return home, ids


def make(profile, tmp_path):
    home, ids = profile
    backup = tmp_path / "backup"
    create_snapshot(home, backup, [ids[0]], version=VERSION)
    return backup


def test_selective_snapshot_and_restore_preserve_rows_without_host_secrets(profile, tmp_path):
    home, ids = profile
    source_hashes = {p.relative_to(home): module.digest(p) for p in home.rglob("*") if p.is_file()}
    backup = make(profile, tmp_path)
    manifest = verify_snapshot(backup, version=VERSION)
    assert set(manifest["threads"]) == {ids[0]}
    assert set(manifest["files"]) == set(module.DATABASES) | {f"sessions/rollout-{ids[0]}.jsonl"}
    assert not (backup / "auth.json").exists() and not (backup / "config.toml").exists()
    restored = tmp_path / "restored"
    restore_snapshot(backup, restored, version=VERSION)
    with sqlite3.connect(restored / "state_5.sqlite") as db:
        assert db.execute("SELECT id,rollout_path FROM threads").fetchall() == [
            (ids[0], str(restored / "sessions" / f"rollout-{ids[0]}.jsonl"))]
    db.close()
    with sqlite3.connect(restored / "thread_history_1.sqlite") as db:
        assert db.execute("SELECT thread_id,status FROM thread_turns").fetchall() == [(ids[0], "completed")]
        assert ids[1] not in db.execute("SELECT item_json FROM thread_items").fetchone()[0]
    db.close()
    assert {p.relative_to(home): module.digest(p) for p in home.rglob("*") if p.is_file()} == source_hashes
    verify_snapshot(backup, version=VERSION)


@pytest.mark.parametrize("fault", ["version", "duplicate", "missing", "schema", "dependency", "identity"])
def test_refuses_unverifiable_native_selection_before_snapshot(profile, tmp_path, fault):
    home, ids = profile
    selected, version = [ids[0]], VERSION
    if fault == "version":
        version = "other"
    elif fault == "duplicate":
        selected *= 2
    elif fault == "missing":
        selected = [str(uuid4())]
    elif fault == "schema":
        with sqlite3.connect(home / "state_5.sqlite") as db:
            db.execute("ALTER TABLE threads ADD COLUMN future_value TEXT")
        db.close()
    elif fault == "dependency":
        with sqlite3.connect(home / "state_5.sqlite") as db:
            db.execute("UPDATE threads SET project_id='unowned-project' WHERE id=?", (ids[0],))
        db.close()
    else:
        path = home / "sessions" / f"rollout-{ids[0]}.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": ids[1]}}), encoding="utf-8")
    with pytest.raises(SnapshotError):
        create_snapshot(home, tmp_path / "backup", selected, version=version)
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize("fault", ["bytes", "missing", "secret", "incomplete", "traversal", "unrelated"])
def test_invalid_package_cannot_create_restore_directory(profile, tmp_path, fault):
    backup = make(profile, tmp_path)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rollout = next(row["path"] for row in manifest["threads"].values())
    if fault == "bytes":
        (backup / rollout).write_bytes(b"changed")
    elif fault == "missing":
        (backup / rollout).unlink()
    elif fault == "secret":
        (backup / "auth.json").write_text("unexpected", encoding="utf-8")
    elif fault == "incomplete":
        (backup / "INCOMPLETE").write_text("failed", encoding="utf-8")
    elif fault == "traversal":
        row = next(iter(manifest["threads"].values()))
        row["path"] = "../outside.jsonl"
        manifest["files"]["../outside.jsonl"] = manifest["files"].pop(rollout)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        with sqlite3.connect(backup / "state_5.sqlite") as db:
            db.execute("INSERT INTO projects(id,name,position,created_at_ms,updated_at_ms) VALUES ('private','unrelated',1,1,1)")
        db.close()
        manifest["files"]["state_5.sqlite"]["sha256"] = module.digest(backup / "state_5.sqlite")
        manifest["files"]["state_5.sqlite"]["bytes"] = (backup / "state_5.sqlite").stat().st_size
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SnapshotError):
        restore_snapshot(backup, tmp_path / "restored", version=VERSION)
    assert not (tmp_path / "restored").exists()


def test_never_overwrites_existing_snapshot_or_profile(profile, tmp_path):
    home, ids = profile
    backup = make(profile, tmp_path)
    with pytest.raises(SnapshotError, match="unsafe_snapshot_destination"):
        create_snapshot(home, backup, [ids[0]], version=VERSION)
    with pytest.raises(SnapshotError, match="unsafe_restore_destination"):
        restore_snapshot(backup, home, version=VERSION)
    assert (home / "auth.json").read_text(encoding="utf-8") == "do-not-export-secret"


def test_concurrent_rollout_change_leaves_incomplete_unrestorable_package(profile, tmp_path, monkeypatch):
    home, ids = profile
    copy = module.shutil.copyfile
    def changing(source, target):
        result = copy(source, target)
        if str(source).endswith(".jsonl"):
            with Path(source).open("a", encoding="utf-8") as stream:
                stream.write("\n")
        return result
    monkeypatch.setattr(module.shutil, "copyfile", changing)
    with pytest.raises(SnapshotError, match="native_history_changed"):
        create_snapshot(home, tmp_path / "backup", [ids[0]], version=VERSION)
    assert (tmp_path / "backup/INCOMPLETE").exists()
    with pytest.raises(SnapshotError, match="incomplete_snapshot"):
        restore_snapshot(tmp_path / "backup", tmp_path / "restored", version=VERSION)


def test_copy_corruption_never_marks_restored_profile_complete(profile, tmp_path, monkeypatch):
    backup = make(profile, tmp_path)
    copy = module.shutil.copyfile
    def corrupt(source, target):
        result = copy(source, target)
        with Path(target).open("ab") as stream:
            stream.write(b"corrupt")
        return result
    monkeypatch.setattr(module.shutil, "copyfile", corrupt)
    with pytest.raises(SnapshotError, match="restore_hash_mismatch"):
        restore_snapshot(backup, tmp_path / "restored", version=VERSION)
    assert (tmp_path / "restored/INCOMPLETE").exists()


def test_cli_verifies_relative_snapshot_path(profile, tmp_path, monkeypatch, capsys):
    backup = make(profile, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["snapshot", "verify", "--source", "backup", "--version", VERSION])
    module.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "verified" and result["threads"] == 1
