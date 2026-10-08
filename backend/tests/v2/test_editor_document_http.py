import copy
import io
from pathlib import Path
from uuid import uuid4
from PIL import Image
from sqlalchemy import select, func
from app.assets.storage import upload_asset
from app.core.config import get_settings
from app.design.models import Branch, Design, EditorCheckpoint, EditorSaveReceipt, Revision, Wallet
from .test_api import api
from .test_migrations import migration_database
from .test_editor_document_contract import CASES, altered

async def test_invalid_engine_documents_reject_before_any_save_side_effect(api):
    client, factory, owner, design, branch = api
    stream = io.BytesIO()
    Image.new("RGB", (640, 480), "coral").save(stream, format="PNG")
    asset = upload_asset(stream.getvalue(), owner, Path(get_settings().v2_storage_dir))
    async with factory.begin() as db:
        db.add(asset)
        await db.flush()
        revision = Revision(design_id=design, branch_id=branch, clean_asset_id=asset.id,
                            root_source_asset_id=asset.id, source_kind="manual", recipe={})
        db.add(revision)
        await db.flush()
        target = await db.get(Branch, branch)
        target.current_revision_id, target.state_version = revision.id, 1

    async def state():
        async with factory() as db:
            design_row, wallet, branch_row = await db.get(Design, design), await db.get(Wallet, owner), await db.get(Branch, branch)
            counts = [await db.scalar(select(func.count()).select_from(table))
                      for table in (Revision, EditorCheckpoint, EditorSaveReceipt)]
            return counts, design_row.event_sequence, design_row.updated_at, wallet.balance, wallet.reserved, branch_row.state_version

    def real_root(document):
        document["root"].update(assetId=asset.id, sha256=asset.sha256)
        document["baseRevision"] = revision.id
        binding = document["maskDocument"]["binding"]
        if binding and binding["sourceKey"] == "root":
            binding["sourceKey"] = asset.id
        return document

    before = await state()
    request_id = str(uuid4())
    path = f"/api/v2/branches/{branch}/editor-saves"
    for case in CASES["invalid"]:
        request = {"client_request_id": request_id, "expected_branch_version": 1,
                   "clean_asset_id": asset.id, "document": real_root(altered(case))}
        response = await client.post(path, json=request)
        assert response.status_code == 422, (case["name"], response.status_code, response.text)
    assert await state() == before

    # Invalid submissions neither consume the request id nor create a checkpoint.
    request = {"client_request_id": request_id, "expected_branch_version": 1,
               "clean_asset_id": asset.id, "document": real_root(copy.deepcopy(CASES["valid"][-1]["document"]))}
    valid = await client.post(path, json=request)
    assert valid.status_code == 200, valid.text
    assert valid.json()["disposition"] == "checkpoint"
    replay = await client.post(path, json=request)
    assert replay.json() == valid.json()
    after = await state()
    assert after[0] == [1, 1, 1]
    assert after[1:] == before[1:]
