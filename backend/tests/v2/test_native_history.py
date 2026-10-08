"""Synthetic native SQLite/rollouts and disposable PostgreSQL; no model or E2E."""
from datetime import timedelta
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import select

from app.design.models import Design, Operation
from app.maintenance import native_history as module, native_history_store as native
from app.maintenance.orphan_assets import CleanupError
from .test_codex_snapshot import profile, VERSION
from .test_operations import factory
from .test_migrations import migration_database
from .test_trash_assets import prepared
from .test_legacy_inventory import fingerprints

REASON = "Apply approved native history retention"


def capture(home, ids):
    return native.inspect_store(home, ids, VERSION)[0]


def assert_other(home, identity):
    with sqlite3.connect(home/"state_5.sqlite") as db:
        assert db.execute("SELECT id FROM threads WHERE id=?", (identity,)).fetchone()
    db.close()
    with sqlite3.connect(home/"thread_history_1.sqlite") as db:
        assert db.execute("SELECT item_json FROM thread_items WHERE thread_id=?", (identity,)).fetchone()
    db.close()
    assert (home/"sessions"/f"rollout-{identity}.jsonl").exists()
    assert (home/"auth.json").read_text() == "do-not-export-secret"


def test_native_store_selective_delete_and_retry_preserve_other_lines(profile):
    home, ids = profile
    lines = [json.dumps({"session_id": key, "text": "private prompt"}, indent=None).encode()+b"\n" for key in ids]
    (home/"history.jsonl").write_bytes(b"".join(lines))
    (home/"session_index.jsonl").write_text("".join(json.dumps({"id": key, "thread_name": "private"})+"\n" for key in ids), encoding="utf-8")
    proof = capture(home, [ids[0]])
    assert "private" not in json.dumps(proof) and "do-not-export" not in json.dumps(proof)
    native.purge_store(home, [ids[0]], VERSION, proof)
    assert (home/"history.jsonl").read_bytes() == lines[1]
    assert json.loads((home/"session_index.jsonl").read_text())["id"] == ids[1]
    assert not (home/"sessions"/f"rollout-{ids[0]}.jsonl").exists()
    assert_other(home, ids[1])
    native.purge_store(home, [ids[0]], VERSION, proof)


@pytest.mark.parametrize("where", ["sqlite", "rollout", "unknown_rollout", "index", "escaped"])
def test_native_cross_thread_references_protect_whole_batch(profile, where):
    home, ids = profile
    value = json.dumps({"parent": ids[0]})
    if where == "sqlite":
        with sqlite3.connect(home/"thread_history_1.sqlite") as db:
            db.execute("UPDATE thread_items SET item_json=? WHERE thread_id=?", (value, ids[1]))
        db.close()
    else:
        path = home/"sessions"/f"rollout-{ids[1]}.jsonl"
        if where == "unknown_rollout": path = home/"sessions"/"unknown.jsonl"
        if where == "index": path = home/"history.jsonl"
        if where == "escaped": value = value.replace("-", "\\u002d")
        with path.open("a", encoding="utf-8") as stream: stream.write(value+"\n")
    with pytest.raises(CleanupError, match="referenced"): capture(home, [ids[0]])
    assert_other(home, ids[0])


@pytest.mark.parametrize("fault", ["bytes", "row", "schema", "missing", "reference", "version", "index"])
def test_changed_native_sources_refused_before_any_deletion(profile, fault):
    home, ids = profile
    (home/"history.jsonl").write_text(json.dumps({"session_id": ids[0], "text":"old"})+"\n", encoding="utf-8")
    proof = capture(home, [ids[0]])
    version = VERSION
    if fault in {"bytes", "reference"}:
        key = ids[0] if fault == "bytes" else ids[1]
        with (home/"sessions"/f"rollout-{key}.jsonl").open("a",encoding="utf-8") as stream:
            stream.write(json.dumps({"text":ids[0]})+"\n")
    elif fault in {"row","schema"}:
        with sqlite3.connect(home/"state_5.sqlite") as db:
            if fault == "row": db.execute("UPDATE threads SET title='changed' WHERE id=?", (ids[0],))
            else: db.execute("ALTER TABLE threads ADD COLUMN future_field TEXT")
        db.close()
    elif fault == "missing": (home/"sessions"/f"rollout-{ids[0]}.jsonl").unlink()
    elif fault == "index": (home/"history.jsonl").write_text(json.dumps({"session_id":ids[0],"text":"new"})+"\n",encoding="utf-8")
    else: version = "unsupported"
    with pytest.raises((CleanupError, ValueError)):
        native.purge_store(home, [ids[0]], version, proof)
    with sqlite3.connect(home/"state_5.sqlite") as db:
        assert db.execute("SELECT id FROM threads WHERE id=?", (ids[0],)).fetchone()
    db.close()


@pytest.mark.parametrize("stage", ["second_store", "rollout"])
def test_partial_native_progress_can_retry(profile, monkeypatch, stage):
    home, ids = profile
    proof = capture(home, [ids[0]])
    with monkeypatch.context() as patch:
        if stage == "second_store":
            original = native.delete_rows
            def fail(db, name, selected):
                if name == "thread_history_1.sqlite": raise OSError("synthetic failure")
                return original(db, name, selected)
            patch.setattr(native, "delete_rows", fail)
        else:
            original = Path.unlink
            def fail(path, *args, **kwargs):
                if path.suffix == ".jsonl": raise OSError("synthetic failure")
                return original(path, *args, **kwargs)
            patch.setattr(Path, "unlink", fail)
        with pytest.raises(OSError): native.purge_store(home, [ids[0]], VERSION, proof)
    native.purge_store(home, [ids[0]], VERSION, proof)
    assert_other(home, ids[1])


async def setup(factory, tmp_path, ids):
    owner, revision, asset, cfg, now = await prepared(factory, tmp_path)
    cfg.v2_native_history_cleanup_enabled = True
    cfg.v2_native_history_grace_days = 1
    cfg.v2_runtime_retention_days = 1
    cfg.v2_runtime_dir = str(tmp_path/"runtime")
    Path(cfg.v2_runtime_dir).mkdir()
    cfg.v2_codex_version = VERSION
    async with factory.begin() as db:
        (await db.get(Operation, revision.operation_id)).upstream_thread_id = ids[0]
        design = await db.get(Design, revision.design_id)
        design.content_purged_at = now
        design.retained_resources = {"native_threads":[{"thread_id":key} for key in ids]}
    return owner, revision, cfg, now


async def inspect(factory, cfg, home, now):
    async with factory.begin() as db:
        return await module.inspect_history(db, cfg, profile=home, now=now, writers_stopped=True)


async def retire(factory, cfg, home, now, owner, report):
    async with factory.begin() as db:
        return await module.retire_history(db, cfg, report["digest"], owner, REASON, profile=home, now=now, writers_stopped=True)


async def purge(factory, cfg, home, now, owner, batch):
    async with factory.begin() as db:
        return await module.purge_history(db, cfg, batch["receipt_id"], owner, REASON, profile=home, now=now, writers_stopped=True)


async def test_committed_lifecycle_audit_backup_tombstone_and_idempotency(factory, tmp_path, profile):
    from app.maintenance.container_backup import owned_threads
    home, ids = profile
    owner, revision, cfg, now = await setup(factory, tmp_path, ids[:1])
    before = await fingerprints(factory)
    report = await inspect(factory, cfg, home, now)
    assert report["threads"] == {ids[0]: owner}
    batch = await retire(factory, cfg, home, now, owner, report)
    assert await retire(factory, cfg, home, now, owner, report) == batch
    with pytest.raises(CleanupError, match="grace"): await purge(factory, cfg, home, now, owner, batch)
    result = await purge(factory, cfg, home, now+timedelta(days=2), owner, batch)
    assert await purge(factory, cfg, home, now+timedelta(days=2), owner, batch) == result
    async with factory() as db:
        assert await owned_threads(db) == {}
        assert await owned_threads(db, include_retired=True) == {ids[0]: owner}
        assert await module.retired_threads(db) == {ids[0]: result["receipt_id"]}
    assert_other(home, ids[1])
    after = await fingerprints(factory)
    assert {k:v for k,v in before.items() if k != "v2_admin_actions"} == {k:v for k,v in after.items() if k != "v2_admin_actions"}


@pytest.mark.parametrize("change", ["business", "profile", "disabled", "policy"])
async def test_new_reference_scope_policy_or_switch_refuses_cleanup(factory, tmp_path, profile, change):
    from app.design.service import create_design
    from uuid import uuid4
    home, ids = profile
    owner, revision, cfg, now = await setup(factory, tmp_path, ids[:1])
    batch = await retire(factory, cfg, home, now, owner, await inspect(factory, cfg, home, now))
    target = home
    if change == "business":
        async with factory.begin() as db:
            design = await create_design(db, owner, str(uuid4()), "active")
            from app.design.models import Branch
            (await db.get(Branch, design.last_branch_id)).context_seed = {"unrecognized":ids[0]}
    elif change == "profile": target = tmp_path/"other"; target.mkdir()
    elif change == "disabled": cfg.v2_native_history_cleanup_enabled = False
    else: cfg.v2_native_history_grace_days = 100
    with pytest.raises(CleanupError): await purge(factory, cfg, target, now+timedelta(days=2), owner, batch)
    assert_other(home, ids[0])


async def test_uncommitted_retirement_cannot_delete(factory, tmp_path, profile):
    home, ids = profile
    owner, _, cfg, now = await setup(factory, tmp_path, ids[:1])
    report = await inspect(factory, cfg, home, now)
    async with factory.begin() as db:
        batch = await module.retire_history(db, cfg, report["digest"], owner, REASON, profile=home, now=now, writers_stopped=True)
        with pytest.raises(CleanupError, match="commit"):
            await module.purge_history(db, cfg, batch["receipt_id"], owner, REASON, profile=home, now=now+timedelta(days=2), writers_stopped=True)
    assert_other(home, ids[0])


def test_rollout_path_reference_without_thread_uuid_is_protected(profile):
    home, ids = profile
    old = home/"sessions"/f"rollout-{ids[0]}.jsonl"
    moved = old.with_name("selected-history.jsonl")
    old.rename(moved)
    with sqlite3.connect(home/"state_5.sqlite") as db:
        db.execute("UPDATE threads SET rollout_path=? WHERE id=?", (str(moved), ids[0]))
        db.execute("UPDATE threads SET title=? WHERE id=?", (moved.name, ids[1]))
    db.close()
    with pytest.raises(CleanupError, match="referenced"): capture(home, [ids[0]])


async def test_backup_dependencies_include_committed_native_absence(factory, tmp_path, profile):
    from app.maintenance.backup_bundle import dependencies
    home, ids = profile
    owner, _, cfg, now = await setup(factory, tmp_path, ids[:1])
    roots = {"runtime": Path(cfg.v2_runtime_dir), "assets": Path(cfg.v2_storage_dir)}
    batch = await retire(factory, cfg, home, now, owner, await inspect(factory,cfg,home,now))
    async with factory() as db:
        _, threads, absent = await dependencies(db, roots)
        assert threads == ids[:1] and not any(item.get("state") == "retired_native_history" for item in absent)
    result = await purge(factory, cfg, home, now+timedelta(days=2), owner, batch)
    async with factory() as db:
        _, threads, absent = await dependencies(db, roots)
        assert threads == []
        assert {"thread_id":ids[0], "state":"retired_native_history", "receipt_id":result["receipt_id"]} in absent


async def test_production_profile_binding_and_stopped_recheck(factory, tmp_path, profile, monkeypatch):
    import shutil
    from .test_runtime_retention_production import production
    home, ids = profile
    owner, _, cfg, now = await setup(factory, tmp_path, ids[:1])
    base, state = production(cfg, owner, Path(cfg.v2_runtime_dir), tmp_path, monkeypatch)
    bound = base/owner/"profile"
    shutil.copytree(home, bound, dirs_exist_ok=True)
    with sqlite3.connect(bound/"state_5.sqlite") as db:
        for key in ids:
            db.execute("UPDATE threads SET rollout_path=? WHERE id=?", (str(bound/"sessions"/f"rollout-{key}.jsonl"),key))
    db.close()
    with pytest.raises(CleanupError, match="scope_mismatch"): await inspect(factory,cfg,home,now)
    report = await inspect(factory,cfg,None,now)
    assert report["owner_id"] == owner and report["native_profile"] == str(bound.resolve())
    batch = await retire(factory,cfg,None,now,owner,report)
    state["fail_at"] = 2
    with pytest.raises(CleanupError, match="stop_unconfirmed"):
        await purge(factory,cfg,None,now+timedelta(days=2),owner,batch)
    assert_other(bound, ids[0])
    state.pop("fail_at")
    await purge(factory,cfg,None,now+timedelta(days=2),owner,batch)
    assert_other(bound, ids[1])
    assert_other(home, ids[0])
    assert set(state["owners"]) == {owner}


async def test_foreign_or_unexpired_design_reference_prevents_retirement(factory,tmp_path,profile):
    from .test_operations import setup as account
    from app.design.models import Branch
    home, ids = profile
    owner, _, cfg, now = await setup(factory,tmp_path,ids[:1])
    # Unknown field in another account is not interpreted as ownership; it still protects.
    async with factory.begin() as db:
        from app.admin.v2_controls import set_control
        await set_control(db,"global",False)
    _, _, branch = await account(factory)
    async with factory.begin() as db:
        (await db.get(Branch,branch)).context_seed = {"unknown_consumer":ids[0]}
        await set_control(db,"global",True)
    with pytest.raises(CleanupError, match="no_eligible"): await inspect(factory,cfg,home,now)
    assert_other(home,ids[0])


async def test_json_reference_alone_cannot_authorize_personal_thread_deletion(factory,tmp_path,profile):
    home, ids = profile
    owner, _, cfg, now = await setup(factory,tmp_path,ids)
    report = await inspect(factory,cfg,home,now)
    assert set(report["threads"]) == {ids[0]}
    batch = await retire(factory,cfg,home,now,owner,report)
    await purge(factory,cfg,home,now+timedelta(days=2),owner,batch)
    assert_other(home,ids[1])
