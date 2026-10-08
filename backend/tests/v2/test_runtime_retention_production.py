"""Isolated PG/files and synthetic Docker/S3; never starts a real runtime."""
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
import pytest
from sqlalchemy import select

from app.admin.v2_controls import set_control
from app.assets.repository import StorageUnavailable
from app.codex_runtime.profile_lock import ProfileLease, ProfileLockError
from app.design.models import Asset, Operation, Revision, AdminAction
from app.design.service import ApplicationError
from app.maintenance import runtime_retention as retention
from app.maintenance.orphan_assets import CleanupError, digest
from .test_operations import factory
from .test_migrations import migration_database
from .test_runtime_retention import prepared, inspect, retire, purge
from .test_remote_retention import bind
from .test_legacy_inventory import fingerprints


class StoppedRuntime:
    def __init__(self, settings, base, state):
        self.owner = settings.v2_worker_user_id
        self.work_root = base / self.owner / "work"
        self.profile = base / self.owner / "profile"
        self.state = state
        self.held = False
        self.checks = 0

    @asynccontextmanager
    async def operation(self):
        lease = ProfileLease(self.profile).acquire()
        self.held = True
        self.state.setdefault("owners", []).append(self.owner)
        try:
            yield
        finally:
            self.held = False
            lease.release(clean=not self.state.get("running"))

    async def verify_stopped(self):
        assert self.held
        self.checks += 1
        if self.state.get("running") or self.checks == self.state.get("fail_at"):
            raise CleanupError("container_stop_unconfirmed")


def production(settings, owner, work, tmp_path, monkeypatch):
    base = tmp_path / "accounts"
    account = base / owner
    account.mkdir(parents=True)
    (account / "profile").mkdir()
    (account / "profile" / "auth.json").write_text("synthetic credential", encoding="utf-8")
    (account / "inputs").mkdir()
    (account / "inputs" / "keep.png").write_bytes(b"input fixture")
    shutil.copytree(work, account / "work")
    settings.app_env = "production"
    settings.v2_runtime_mode = "container"
    settings.v2_container_root = "/srv/kigcraft/runtime"
    settings.v2_container_user_ids = [owner]
    settings.v2_worker_user_id = owner
    settings.v2_container_image = "runtime@sha256:" + "a" * 64
    settings.v2_egress_image = "egress@sha256:" + "b" * 64
    settings.v2_egress_hosts = ["model.example.com"]
    original = retention.root_path
    def root(path):
        value = str(path).replace("\\", "/")
        prefix = settings.v2_container_root
        return original(base / value[len(prefix):].lstrip("/")) if value == prefix or value.startswith(prefix + "/") else original(path)
    state = {}
    monkeypatch.setattr(retention, "root_path", root)
    monkeypatch.setattr(retention, "ContainerRuntime", lambda cfg: StoppedRuntime(cfg, base, state))
    return base, state


async def test_account_scoped_retirement_preserves_other_account_and_profile(factory, tmp_path, monkeypatch):
    first = tmp_path / "first"; first.mkdir()
    second = tmp_path / "second"; second.mkdir()
    owner, opid, cfg, now, workspace, image = await prepared(factory, first, delivered=True)
    async with factory.begin() as db:
        await set_control(db, "global", False)
    other, otherop, _, _, otherworkspace, _ = await prepared(factory, second)
    base, state = production(cfg, owner, workspace.parent, tmp_path, monkeypatch)
    # Another configured account has its own immutable folder mapping.
    cfg.v2_container_user_ids = [owner, other]
    (base / other).mkdir()
    shutil.copytree(otherworkspace.parent, base / other / "work")
    before = await fingerprints(factory)
    report = await inspect(factory, cfg, now=now, limit=1)
    assert report["format"] == 2 and report["owner_id"] == owner
    assert report["next_after"] is None and set(report["operations"]) == {opid}
    assert all(item["key"].startswith(opid + "/") for item in report["candidates"])
    assert await fingerprints(factory) == before
    batch = await retire(factory, cfg, owner, report, now=now, limit=1)
    await purge(factory, cfg, owner, batch, now=now)
    assert set(state["owners"]) == {owner}
    assert not (base / owner / "work" / opid / "staging" / image.path.name).exists()
    assert (base / other / "work" / otherop / "review" / "nested" / "temporary.txt").exists()
    assert (base / owner / "profile" / "auth.json").read_text() == "synthetic credential"
    assert (base / owner / "inputs" / "keep.png").exists()
    async with factory() as db:
        operation = await db.get(Operation, opid)
        assert await retention.retired_staging(db, opid, operation.staged_outputs["main_image"]) == batch["receipt_id"]


@pytest.mark.parametrize("fault", ["running", "busy", "owner_missing", "owner_foreign", "root_mismatch"])
async def test_production_boundary_refuses_before_inventory(factory, tmp_path, monkeypatch, fault):
    owner, opid, cfg, now, workspace, image = await prepared(factory, tmp_path)
    base, state = production(cfg, owner, workspace.parent, tmp_path, monkeypatch)
    lease = None
    if fault == "running": state["running"] = True
    elif fault == "busy": lease = ProfileLease(base / owner / "profile").acquire()
    elif fault == "owner_missing": cfg.v2_worker_user_id = None
    elif fault == "owner_foreign": cfg.v2_worker_user_id = "00000000-0000-0000-0000-000000000001"
    else:
        def wrong(cfg):
            runtime = StoppedRuntime(cfg, base, state)
            runtime.work_root = base / owner / "inputs"
            return runtime
        monkeypatch.setattr(retention, "ContainerRuntime", wrong)
    try:
        with pytest.raises((CleanupError, ProfileLockError)):
            await inspect(factory, cfg, now=now)
        assert (base / owner / "work" / opid / "staging" / image.path.name).exists()
    finally:
        if lease: lease.release(clean=True)


async def test_stopped_state_rechecked_before_first_delete(factory, tmp_path, monkeypatch):
    owner, opid, cfg, now, workspace, image = await prepared(factory, tmp_path)
    base, state = production(cfg, owner, workspace.parent, tmp_path, monkeypatch)
    batch = await retire(factory, cfg, owner, await inspect(factory, cfg, now=now), now=now)
    state["fail_at"] = 2
    with pytest.raises(CleanupError, match="container_stop_unconfirmed"):
        await purge(factory, cfg, owner, batch, now=now)
    assert (base / owner / "work" / opid / "staging" / image.path.name).exists()
    assert (base / owner / "work" / opid / "review" / "nested" / "temporary.txt").exists()


@pytest.mark.parametrize("mode", ["unversioned", "Enabled"])
async def test_remote_master_read_without_cache_and_no_remote_mutation(factory, tmp_path, monkeypatch, mode):
    owner, opid, cfg, now, workspace, image = await prepared(factory, tmp_path, delivered=True)
    async with factory() as db:
        revision = await db.scalar(select(Revision).where(Revision.operation_id == opid))
        asset = await db.get(Asset, revision.clean_asset_id)
    objects = bind(cfg, tmp_path, monkeypatch, asset, Path(cfg.v2_storage_dir), mode)
    base, _ = production(cfg, owner, workspace.parent, tmp_path, monkeypatch)
    report = await inspect(factory, cfg, now=now)
    assert report["asset_target"].startswith("minio:")
    batch = await retire(factory, cfg, owner, report, now=now)
    await purge(factory, cfg, owner, batch, now=now)
    assert not Path(cfg.v2_storage_dir).exists()
    assert objects.versions and all(method == "get" for method, _ in objects.calls)
    assert len(objects.calls) == 3
    assert not (base / owner / "work" / opid / "staging" / image.path.name).exists()


@pytest.mark.parametrize("fault", ["missing", "corrupt", "new_target", "foreign_asset"])
async def test_remote_failure_preserves_all_runtime_files(factory, tmp_path, monkeypatch, fault):
    owner, opid, cfg, now, workspace, image = await prepared(factory, tmp_path, delivered=True)
    async with factory() as db:
        revision = await db.scalar(select(Revision).where(Revision.operation_id == opid))
        asset = await db.get(Asset, revision.clean_asset_id)
    root = Path(cfg.v2_storage_dir)
    objects = bind(cfg, tmp_path, monkeypatch, asset, root)
    # A valid warm cache must never hide a missing/corrupt remote master.
    cfg.v2_storage_dir = str(root)
    batch = await retire(factory, cfg, owner, await inspect(factory, cfg, now=now), now=now)
    if fault == "missing": objects.versions.clear()
    elif fault == "corrupt": objects.versions[None] = b"x" * asset.byte_size
    elif fault == "new_target": cfg.v2_s3_prefix = "another"
    else:
        from app.db.models import User
        from app.auth.models import UserRole
        async with factory.begin() as db:
            foreign = User(email="other-runtime@example.test", password_hash="fixture", role=UserRole.user)
            db.add(foreign); await db.flush()
            (await db.get(Asset, asset.id)).owner_id = foreign.id
    with pytest.raises((CleanupError, ApplicationError, StorageUnavailable)):
        await purge(factory, cfg, owner, batch, now=now)
    assert image.path.exists() and (workspace / "review" / "nested" / "temporary.txt").exists()
    assert (root / asset.object_key).exists()


async def test_legacy_manifest_remains_local_only(factory, tmp_path, monkeypatch):
    owner, opid, cfg, now, workspace, image = await prepared(factory, tmp_path)
    batch = await retire(factory, cfg, owner, await inspect(factory, cfg, now=now), now=now)
    async with factory.begin() as db:
        row = await db.get(AdminAction, batch["receipt_id"])
        value = {key: item for key, item in row.result["manifest"].items()
                 if key not in {"digest", "owner_id", "runtime_mode", "asset_target"}}
        value["format"] = 1; value["digest"] = digest(value)
        row.result = {**row.result, "manifest": value}
    await purge(factory, cfg, owner, batch, now=now)
    assert not image.path.exists()


@pytest.mark.parametrize("fault", ["legacy_receipt", "changed_owner"])
async def test_production_receipt_cannot_authorize_different_scope(factory, tmp_path, monkeypatch, fault):
    owner, opid, cfg, now, workspace, image = await prepared(factory, tmp_path)
    base, _ = production(cfg, owner, workspace.parent, tmp_path, monkeypatch)
    batch = await retire(factory, cfg, owner, await inspect(factory, cfg, now=now), now=now)
    async with factory.begin() as db:
        if fault == "legacy_receipt":
            row = await db.get(AdminAction, batch["receipt_id"])
            value = {key: item for key, item in row.result["manifest"].items()
                     if key not in {"digest", "owner_id", "runtime_mode", "asset_target"}}
            value["format"] = 1; value["digest"] = digest(value)
            row.result = {**row.result, "manifest": value}
        else:
            from uuid import uuid4
            row = await db.get(AdminAction, batch["receipt_id"])
            value = {key: item for key, item in row.result["manifest"].items() if key != "digest"}
            value["owner_id"] = str(uuid4())
            value["digest"] = digest(value)
            row.result = {**row.result, "manifest": value}
    with pytest.raises(CleanupError, match="retirement_scope_mismatch|operation_unconfirmed"):
        await purge(factory, cfg, owner, batch, now=now)
    assert (base / owner / "work" / opid / "staging" / image.path.name).exists()
    assert (base / owner / "work" / opid / "review" / "nested" / "temporary.txt").exists()
