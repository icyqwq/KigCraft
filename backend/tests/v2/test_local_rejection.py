"""HTTP admission and worker delivery must reject incompatible local image dimensions."""
import hashlib

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.assets.storage import upload_asset
from app.design.models import (
    Asset, Branch, DomainEvent, InputRecord, LedgerEntry, Message, Operation, Quote, Revision, Wallet,
)
from .test_api import api
from .test_local_edit import accepted, draft, local_settings
from .test_local_images import png
from .test_migrations import migration_database
from .test_operations import factory


async def fingerprint(factory, owner, branch):
    async with factory() as db:
        counts = {
            model.__name__: await db.scalar(select(func.count()).select_from(model))
            for model in (Asset, Branch, DomainEvent, InputRecord, LedgerEntry, Message, Operation, Quote, Revision)
        }
        wallet = await db.get(Wallet, owner)
        current = await db.get(Branch, branch)
        return counts, (wallet.balance, wallet.reserved, wallet.version), (current.current_revision_id, current.state_version)


@pytest.mark.parametrize("mode", ["lock_outside", "preserve"])
@pytest.mark.parametrize("size", [(80, 80), (160, 80), (320, 320)])
async def test_http_mask_dimensions_rejected_without_any_business_write(api, tmp_path, mode, size):
    client, factory, owner, design, branch = api
    _, _, _, request, _ = await draft(factory, tmp_path, mode, identity=(owner, design, branch))
    valid = request.model_dump(mode="json", exclude={"quote_id"})
    price = await client.post(f"/api/v2/branches/{branch}/quotes", json=valid)
    assert price.status_code == 200, price.text
    async with factory.begin() as db:
        mask = upload_asset(png(Image.new("RGB", size, "white")), owner, tmp_path / "assets")
        db.add(mask)
        await db.flush()
        mask_id = mask.id
    invalid = {**valid, "mask_asset_id": mask_id, "quote_id": price.json()["id"]}
    original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (tmp_path / "assets").rglob("*") if path.is_file()}
    before = await fingerprint(factory, owner, branch)
    for endpoint in ("quotes", "turns"):
        payload = invalid if endpoint == "turns" else {key: value for key, value in invalid.items() if key != "quote_id"}
        response = await client.post(f"/api/v2/branches/{branch}/{endpoint}", json=payload)
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "local_mask_size_mismatch"
        assert await fingerprint(factory, owner, branch) == before
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in original} == original


@pytest.mark.parametrize("size", [(80, 80), (160, 80), (320, 320)])
async def test_worker_output_dimensions_fail_without_resizing_or_settling(factory, tmp_path, monkeypatch, size):
    worker, runtime, owner, branch, base, opid, _ = await accepted(
        factory, tmp_path, monkeypatch, image=Image.new("RGB", size, "coral"),
    )
    original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (tmp_path / "assets").rglob("*") if path.is_file()}
    assert await worker.run_once() == opid
    async with factory() as db:
        operation = await db.get(Operation, opid)
        assert (operation.state, operation.error_code, operation.upstream_status) == ("failed", "local_output_size_mismatch", "completed")
        assert operation.settled_units == 0 and operation.reservation_closed
        assert not operation.staged_outputs
        assert (await db.get(Branch, branch)).current_revision_id == base.id
        assert await db.scalar(select(func.count()).select_from(Revision).where(Revision.operation_id == opid)) == 0
        wallet = await db.get(Wallet, owner)
        assert (wallet.balance, wallet.reserved) == (5, 0)
        entries = (await db.scalars(select(LedgerEntry).where(LedgerEntry.operation_id == opid))).all()
        assert sorted(entry.kind for entry in entries) == ["release", "reserve"]
    current = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (tmp_path / "assets").rglob("*") if path.is_file()}
    assert current == original
    assert sum(images and method == "turn/start" for images, method, _ in runtime.calls) == 1
