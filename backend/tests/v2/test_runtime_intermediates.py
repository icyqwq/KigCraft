"""Disposable PG/files and synthetic native history only."""
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import select

from app.design.models import AdminAction, Operation
from app.maintenance import runtime_intermediates as module
from app.maintenance import runtime_references as refs
from app.maintenance.orphan_assets import CleanupError
from app.codex_runtime.profile_lock import ProfileLease, ProfileLockError
from .test_operations import factory
from .test_migrations import migration_database
from .test_runtime_retention import prepared
from .test_codex_snapshot import profile, VERSION
from .test_runtime_retention_production import production
from .test_legacy_inventory import fingerprints

REASON = "Reviewed intermediate retention"
EXTRA = "a" * 64 + ".png"
TEMP = ".local-abcdefg1"


async def fixture(factory, tmp_path):
    owner, opid, cfg, now, workspace, image = await prepared(factory, tmp_path, delivered=True)
    cfg.v2_codex_version = VERSION
    for name in (EXTRA, TEMP):
        path = workspace / "staging" / name
        path.write_bytes(b"unregistered intermediate " + name.encode())
        old = (now - timedelta(days=30)).timestamp()
        os.utime(path, (old, old))
    return owner, opid, cfg, now, workspace, image


async def inspect(factory, cfg, home, now):
    async with factory.begin() as db:
        return await module.inspect_intermediates(db, cfg, profile=home, writers_stopped=True, now=now)


async def retire(factory, cfg, home, now, owner, report):
    async with factory.begin() as db:
        return await module.retire_intermediates(db, cfg, report["digest"], owner, REASON,
            profile=home, writers_stopped=True, now=now)


async def purge(factory, cfg, home, now, owner, batch):
    async with factory.begin() as db:
        return await module.purge_intermediates(db, cfg, batch["receipt_id"], owner, REASON,
            profile=home, writers_stopped=True, now=now)


async def test_retirement_grace_cleanup_keeps_registered_and_unknown_files(factory, tmp_path, profile):
    home, _ = profile
    owner, opid, cfg, now, workspace, image = await fixture(factory, tmp_path)
    before = await fingerprints(factory)
    report = await inspect(factory, cfg, home, now)
    assert len(report["candidates"]) == 2 and report["candidate_bytes"] > 0
    assert await fingerprints(factory) == before
    assert "private " not in json.dumps(report) and "do-not-export" not in json.dumps(report)
    batch = await retire(factory, cfg, home, now, owner, report)
    with pytest.raises(CleanupError, match="grace"):
        await purge(factory, cfg, home, now, owner, batch)
    result = await purge(factory, cfg, home, now + timedelta(days=8), owner, batch)
    assert result["count"] == 2
    assert image.path.exists() and (workspace/"staging"/"unknown.bin").exists()
    assert (workspace/"inputs"/"reference.png").exists() and (workspace/"original.png").exists()
    assert not (workspace/"staging"/EXTRA).exists()
    assert not (workspace/"staging"/TEMP).exists()
    assert await purge(factory, cfg, home, now + timedelta(days=8), owner, batch) == result
    after = await fingerprints(factory)
    assert {k:v for k,v in after.items() if k != "v2_admin_actions"} == {k:v for k,v in before.items() if k != "v2_admin_actions"}


@pytest.mark.parametrize("where", ["business", "sqlite", "rollout", "archived", "history", "escaped"])
async def test_references_protect_even_unregistered_or_shared_native_history(factory, tmp_path, profile, where):
    home, ids = profile
    _, opid, cfg, now, workspace, _ = await fixture(factory, tmp_path)
    if where == "business":
        async with factory.begin() as db:
            (await db.get(Operation, opid)).context_before = {"unrecognized_future_field": EXTRA[:-4]}
    elif where == "sqlite":
        with sqlite3.connect(home/"thread_history_1.sqlite") as db:
            db.execute("UPDATE thread_items SET item_json=? WHERE thread_id=?", (json.dumps({"path": EXTRA}), ids[1]))
        db.close()
    else:
        path = home/"sessions"/f"rollout-{ids[1]}.jsonl"
        if where == "archived":
            path = home/"archived_sessions"/"unregistered.jsonl"
            path.parent.mkdir()
        if where == "history": path = home/"history.jsonl"
        value = json.dumps({"text": EXTRA})
        if where == "escaped": value = value.replace("a", "\\u0061")
        with path.open("a", encoding="utf-8") as stream: stream.write(value + "\n")
    report = await inspect(factory, cfg, home, now)
    assert [item["key"].split("/")[-1] for item in report["candidates"]] == [TEMP]
    assert any(item["reason"] == "referenced_file" and item["key"].endswith(EXTRA) for item in report["retained"])


@pytest.mark.parametrize("change", ["reference", "native", "new_descriptor", "bytes", "policy", "profile"])
async def test_new_dependencies_or_mutation_prevent_entire_batch(factory, tmp_path, profile, change):
    home, ids = profile
    owner, opid, cfg, now, workspace, _ = await fixture(factory, tmp_path)
    batch = await retire(factory, cfg, home, now, owner, await inspect(factory, cfg, home, now))
    if change in {"reference", "new_descriptor"}:
        async with factory.begin() as db:
            operation = await db.get(Operation, opid)
            if change == "reference": operation.context_before = {"future": TEMP}
            else: operation.staged_outputs = {**operation.staged_outputs, "future": {"filename": EXTRA}}
    elif change == "native":
        with (home/"sessions"/f"rollout-{ids[0]}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"path": EXTRA}) + "\n")
    elif change == "bytes": (workspace/"staging"/TEMP).write_bytes(b"changed")
    elif change == "policy": cfg.v2_runtime_retention_days = 365
    else:
        home = tmp_path/"different-profile"; home.mkdir()
    with pytest.raises(CleanupError):
        await purge(factory, cfg, home, now + timedelta(days=8), owner, batch)
    assert (workspace/"staging"/EXTRA).exists() and (workspace/"staging"/TEMP).exists()


async def test_purge_requires_commit_and_resumes_after_partial_failure(factory, tmp_path, profile, monkeypatch):
    home, _ = profile
    owner, _, cfg, now, workspace, _ = await fixture(factory, tmp_path)
    report = await inspect(factory, cfg, home, now)
    async with factory.begin() as db:
        batch = await module.retire_intermediates(db, cfg, report["digest"], owner, REASON,
            profile=home, writers_stopped=True, now=now)
        with pytest.raises(CleanupError, match="commit"):
            await module.purge_intermediates(db, cfg, batch["receipt_id"], owner, REASON,
                profile=home, writers_stopped=True, now=now + timedelta(days=8))
    original = Path.unlink
    calls = []
    def fail(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 2: raise OSError("synthetic interruption")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        with pytest.raises(OSError):
            await purge(factory, cfg, home, now + timedelta(days=8), owner, batch)
    assert len(calls) == 2
    assert (await purge(factory, cfg, home, now + timedelta(days=8), owner, batch))["count"] == 2


@pytest.mark.parametrize("fault", ["unknown_schema", "unknown_database", "missing_rollout", "malformed", "hardlink", "busy"])
async def test_unknown_or_busy_native_state_fails_closed(factory, tmp_path, profile, fault):
    home, ids = profile
    owner, _, cfg, now, workspace, _ = await fixture(factory, tmp_path)
    lease = None
    if fault == "unknown_schema":
        with sqlite3.connect(home/"state_5.sqlite") as db: db.execute("CREATE TABLE new_state(data TEXT)")
        db.close()
    elif fault == "unknown_database": (home/"state_99.sqlite").write_bytes(b"future")
    elif fault == "missing_rollout": (home/"sessions"/f"rollout-{ids[0]}.jsonl").unlink()
    elif fault == "malformed": (home/"sessions"/"unfinished.jsonl").write_bytes(b"{")
    elif fault == "hardlink": os.link(workspace/"staging"/TEMP, tmp_path/"linked")
    else: lease = ProfileLease(home).acquire()
    try:
        with pytest.raises((ValueError, ProfileLockError)):
            await inspect(factory, cfg, home, now)
        assert (workspace/"staging"/EXTRA).exists()
    finally:
        if lease: lease.release(clean=True)


async def test_production_uses_bound_profile_and_account_lock(factory, tmp_path, monkeypatch):
    owner, opid, cfg, now, workspace, _ = await fixture(factory, tmp_path)
    base, state = production(cfg, owner, workspace.parent, tmp_path, monkeypatch)
    home = base/owner/"profile"
    report = await inspect(factory, cfg, None, now)
    assert report["owner_id"] == owner and report["native_profile"] == str(home.resolve())
    batch = await retire(factory, cfg, None, now, owner, report)
    state["fail_at"] = 2
    with pytest.raises(CleanupError, match="container_stop"):
        await purge(factory, cfg, None, now + timedelta(days=8), owner, batch)
    assert (base/owner/"work"/opid/"staging"/EXTRA).exists()
    state.clear()
    assert (await purge(factory, cfg, None, now + timedelta(days=8), owner, batch))["count"] == 2


def test_reference_scan_is_bounded_and_leaves_private_sources_unchanged(profile, monkeypatch):
    home, _ = profile
    files = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    monkeypatch.setattr(refs, "MAX_ROWS", 1)
    with pytest.raises(CleanupError, match="too_large"):
        refs.native_references(home, [{"key": "id/staging/" + EXTRA}], VERSION)
    assert all(p.read_bytes() == content for p, content in files.items())


@pytest.mark.parametrize("change", ["original_grace", "increased_grace", "inventory"])
async def test_reviewed_policy_cannot_be_shortened_and_history_changes_require_review(factory, tmp_path, profile, change):
    home, ids = profile
    owner, _, cfg, now, workspace, _ = await fixture(factory, tmp_path)
    if change == "original_grace": cfg.v2_runtime_intermediate_grace_days = 30
    report = await inspect(factory, cfg, home, now)
    if change == "inventory":
        with (home/"sessions"/f"rollout-{ids[0]}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"text": "new unrelated history"}) + "\n")
        with pytest.raises(CleanupError, match="inventory_changed"):
            await retire(factory, cfg, home, now, owner, report)
    else:
        batch = await retire(factory, cfg, home, now, owner, report)
        cfg.v2_runtime_intermediate_grace_days = 30 if change == "increased_grace" else 1
        with pytest.raises(CleanupError, match="grace"):
            await purge(factory, cfg, home, now + timedelta(days=8), owner, batch)
    assert (workspace/"staging"/EXTRA).exists()


def test_unicode_json_in_sqlite_protects_file(profile):
    home, ids = profile
    value = json.dumps({"path": EXTRA}).replace("a", "\\u0061")
    with sqlite3.connect(home/"thread_history_1.sqlite") as db:
        db.execute("UPDATE thread_items SET item_json=? WHERE thread_id=?", (value, ids[0]))
    db.close()
    key = "operation/staging/" + EXTRA
    protected, _ = refs.native_references(home, [{"key": key}], VERSION)
    assert protected == {key}


def test_unparseable_deep_native_json_never_counts_as_unreferenced(profile):
    home, ids = profile
    with sqlite3.connect(home/"thread_history_1.sqlite") as db:
        db.execute("UPDATE thread_items SET item_json=? WHERE thread_id=?", ("[" * 2000 + "0" + "]" * 2000, ids[0]))
    db.close()
    with pytest.raises(CleanupError, match="too_large"):
        refs.native_references(home, [{"key": "id/staging/" + EXTRA}], VERSION)
