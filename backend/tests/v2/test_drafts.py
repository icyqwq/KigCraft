import asyncio
from uuid import uuid4
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from app.design.drafts import SaveDraft, DeleteDraft, save_draft, delete_draft, list_drafts
from app.design.models import Asset, Branch, Design, EditorDraft, Wallet
from app.design.service import ApplicationError
from .test_operations import factory, setup
from .test_manual import base
from .test_migrations import migration_database
from .test_api import api

def request(branch, revision, asset, **changes):
    document = {"schema_version": 1, "source_sha256": asset.sha256, "width": asset.width, "height": asset.height,
        "content": {"recipe": {}, "maskStrokes": [], "localEditNote": "local instruction", "selectedLocalReferenceKeys": [],
            "localUploadedReferenceDescription": "", "extraReferenceDescription": ""},
        "attachments": []}
    return SaveDraft(client_request_id=str(uuid4()), expected_version=0, device_id="test-device",
        branch_id=branch, base_revision_id=revision.id, root_asset_id=asset.id, document=document, **changes)

async def fixture(factory, tmp_path):
    owner, branch, revision = await base(factory, tmp_path)
    async with factory() as db:
        root = await db.get(Asset, revision.root_source_asset_id)
    return owner, branch, revision, root

async def test_draft_retries_are_idempotent_and_do_not_change_design_or_wallet(factory, tmp_path):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    payload = request(branch, revision, root)
    key = str(uuid4())
    async with factory() as db:
        design = await db.get(Design, revision.design_id)
        before = (design.updated_at, design.event_sequence, design.last_branch_id)
    async with factory.begin() as db:
        one = await save_draft(db, owner, key, payload)
    async with factory.begin() as db:
        two = await save_draft(db, owner, key, payload)
        assert two == one == {"id": key, "version": 1, "deleted": False}
        assert (await db.get(Branch, branch)).current_revision_id == revision.id
        assert (await db.get(Branch, branch)).state_version == 1
        design = await db.get(Design, revision.design_id)
        assert (design.updated_at, design.event_sequence, design.last_branch_id) == before
        assert (await db.get(Wallet, owner)).balance == 4
        assert (await list_drafts(db, owner, branch, revision.id))[0]["document"]["content"]["localEditNote"] == "local instruction"
    altered = payload.model_copy(update={"device_id": "changed"})
    with pytest.raises(ApplicationError, match="idempotency_conflict"):
        async with factory.begin() as db:
            await save_draft(db, owner, key, altered)

async def test_draft_concurrent_writes_conflict_and_client_can_keep_a_copy(factory, tmp_path):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    payload = request(branch, revision, root)
    key = str(uuid4())
    async with factory.begin() as db:
        await save_draft(db, owner, key, payload)
    async def write(device):
        try:
            async with factory.begin() as db:
                return await save_draft(db, owner, key, payload.model_copy(update={
                    "client_request_id": str(uuid4()), "expected_version": 1, "device_id": device}))
        except ApplicationError as failure:
            return failure.code
    values = await asyncio.gather(write("A"), write("B"))
    assert values.count("draft_conflict") == 1
    copy = str(uuid4())
    async with factory.begin() as db:
        await save_draft(db, owner, copy, payload.model_copy(update={"client_request_id": str(uuid4())}))
        assert len(await list_drafts(db, owner, branch, revision.id)) == 2

async def test_draft_delete_is_conditional_idempotent_and_cannot_resurrect(factory, tmp_path):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    payload, key = request(branch, revision, root), str(uuid4())
    async with factory.begin() as db:
        await save_draft(db, owner, key, payload)
        await save_draft(db, owner, key, payload.model_copy(update={"client_request_id": str(uuid4()), "expected_version": 1}))
    with pytest.raises(ApplicationError, match="draft_conflict"):
        async with factory.begin() as db:
            await delete_draft(db, owner, key, DeleteDraft(client_request_id=str(uuid4()), expected_version=1))
    deletion = DeleteDraft(client_request_id=str(uuid4()), expected_version=2)
    async with factory.begin() as db:
        receipt = await delete_draft(db, owner, key, deletion)
    async with factory.begin() as db:
        assert await delete_draft(db, owner, key, deletion) == receipt
        assert not await list_drafts(db, owner, branch, revision.id)
        assert (await save_draft(db, owner, key, payload))["version"] == 1  # lost old ACK stays an old ACK
        assert (await db.get(EditorDraft, key)).deleted_at is not None
    with pytest.raises(ApplicationError, match="draft_conflict"):
        async with factory.begin() as db:
            await save_draft(db, owner, key, payload.model_copy(update={"client_request_id": str(uuid4())}))

@pytest.mark.parametrize("field", ["root", "selected", "attachment", "base"])
async def test_draft_rejects_cross_owner_references(factory, tmp_path, field):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    _, _, foreign_revision, foreign_root = await fixture(factory, tmp_path)
    payload = request(branch, revision, root).model_dump(mode="json")
    if field == "root":
        payload["root_asset_id"] = foreign_root.id
    elif field == "base":
        payload["base_revision_id"] = foreign_revision.id
    elif field == "selected":
        payload["document"]["content"]["selectedLocalReferenceKeys"] = [foreign_root.id]
    else:
        payload["document"]["attachments"] = [{"slot": "local", "asset_id": foreign_root.id, "file_name": "ref.png", "last_modified": 0}]
    with pytest.raises(ApplicationError, match="not_found"):
        async with factory.begin() as db:
            await save_draft(db, owner, str(uuid4()), SaveDraft.model_validate(payload))

@pytest.mark.parametrize("field", ["source_sha256", "width", "mask"])
async def test_draft_rejects_wrong_source_binding(factory, tmp_path, field):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    payload = request(branch, revision, root).model_dump(mode="json")
    if field == "mask":
        payload["document"]["content"]["maskBinding"] = {"sourceKey": "wrong", "width": root.width, "height": root.height, "geometry": "{}"}
    else:
        payload["document"][field] = "0"*64 if field == "source_sha256" else root.width + 1
    with pytest.raises(ApplicationError, match="draft_source_mismatch"):
        async with factory.begin() as db:
            await save_draft(db, owner, str(uuid4()), SaveDraft.model_validate(payload))

async def test_draft_http_read_write_delete_and_account_isolation(api):
    from app.design.models import Revision
    from app.auth.sessions import _hash_token
    from app.db.models import AuthSession
    from app.core.config import get_settings
    from app.core.time import utc_now
    from datetime import timedelta
    client, factory, owner, design, branch = api
    async with factory.begin() as db:
        asset = Asset(owner_id=owner, object_key=str(uuid4()), sha256="a"*64, mime_type="image/png",
            kind="reference", width=16, height=16, byte_size=1, status="ready")
        db.add(asset); await db.flush()
        revision = Revision(design_id=design, branch_id=branch, clean_asset_id=asset.id,
            root_source_asset_id=asset.id, source_kind="manual")
        db.add(revision); await db.flush()
        payload = request(branch, revision, asset).model_dump(mode="json")
        base_id = revision.id
    import io
    from PIL import Image
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "coral").save(image, format="PNG")
    uploaded = await client.post("/api/v2/assets/uploads", params={"expected_owner_id": owner},
        files={"file": ("ref.png", image.getvalue(), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    payload["document"]["attachments"] = [{"slot": "local", "asset_id": uploaded.json()["id"],
        "file_name": "ref.png", "last_modified": 7}]
    key = str(uuid4())
    first = await client.put(f"/api/v2/editor-drafts/{key}", json=payload)
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "private, no-store"
    read = await client.get(f"/api/v2/branches/{branch}/editor-drafts", params={"base_revision_id": base_id})
    assert len(read.json()["items"]) == 1
    assert read.json()["items"][0]["document"]["attachments"] == payload["document"]["attachments"]
    assert (await client.get(uploaded.json()["image_url"])).status_code == 200
    unknown_schema = {**payload, "document": {**payload["document"], "schema_version": 99}}
    assert (await client.put(f"/api/v2/editor-drafts/{key}", json=unknown_schema)).status_code == 422
    other, _, _ = await setup(factory)
    token = "draft-other-" + uuid4().hex
    async with factory.begin() as db:
        db.add(AuthSession(user_id=other, session_token_hash=_hash_token(token), expires_at=utc_now()+timedelta(hours=1), created_ip="127.0.0.1"))
    client.cookies.clear(); client.cookies.set(get_settings().session_cookie_name, token)
    assert (await client.get(f"/api/v2/branches/{branch}/editor-drafts", params={"base_revision_id": base_id})).status_code == 404
    assert (await client.put(f"/api/v2/editor-drafts/{key}", json=payload)).status_code == 404
    assert (await client.request("DELETE", f"/api/v2/editor-drafts/{key}", json={"client_request_id": str(uuid4()), "expected_version": 1})).status_code == 404
    import io
    from PIL import Image
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "coral").save(image, format="PNG")
    denied = await client.post("/api/v2/assets/uploads", params={"expected_owner_id": owner},
        files={"file": ("ref.png", image.getvalue(), "image/png")})
    assert denied.status_code == 404
    client.cookies.clear()
    assert (await client.put(f"/api/v2/editor-drafts/{key}", json=payload)).status_code == 401

async def test_local_edit_mode_roundtrips_without_changing_old_receipt_digest(factory, tmp_path):
    from app.design.models import DraftWriteReceipt
    from app.design.service import digest
    owner, branch, revision, root = await fixture(factory, tmp_path)
    old = request(branch, revision, root)
    key = str(uuid4())
    legacy = old.model_dump(mode="json")
    legacy["document"]["content"].pop("localEditMode")
    legacy["document"]["content"].pop("localReferences")
    async with factory.begin() as db:
        await save_draft(db, owner, key, old)
        receipt = await db.get(DraftWriteReceipt, (key, old.client_request_id))
        assert receipt.request_digest == digest({"method": "PUT", "payload": legacy})
        assert "localEditMode" not in (await db.get(EditorDraft, key)).document["content"]
    updated = old.model_dump(mode="json")
    updated.update(client_request_id=str(uuid4()), expected_version=1)
    updated["document"]["content"]["localEditMode"] = "lock_outside"
    async with factory.begin() as db:
        await save_draft(db, owner, key, SaveDraft.model_validate(updated))
        result = (await list_drafts(db, owner, branch, revision.id))[0]
        assert result["document"]["content"]["localEditMode"] == "lock_outside"
        assert (await save_draft(db, owner, key, old))["version"] == 1

def test_draft_rejects_unknown_local_edit_mode():
    from app.design.drafts import DraftContent
    with pytest.raises(ValidationError):
        DraftContent(recipe={}, maskStrokes=[], localEditNote="", selectedLocalReferenceKeys=[],
            localUploadedReferenceDescription="", extraReferenceDescription="", localEditMode="guess")

async def test_unified_references_roundtrip_order_usage_and_owned_files(factory, tmp_path):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    payload = request(branch, revision, root).model_dump(mode="json")
    payload["document"]["schema_version"] = 2
    payload["document"]["content"]["localReferences"] = [
        {"key":"history-ref","assetId":root.id,"usage":"accessory"},
        {"key":"upload-ref","assetId":root.id,"usage":"color","fileName":"color.png","lastModified":7},
    ]
    key = str(uuid4())
    async with factory.begin() as db:
        await save_draft(db, owner, key, SaveDraft.model_validate(payload))
        saved = (await list_drafts(db, owner, branch, revision.id))[0]
        refs = saved["document"]["content"]["localReferences"]
        assert [(item["key"], item["usage"]) for item in refs] == [("history-ref","accessory"),("upload-ref","color")]
        assert refs[1]["fileName"] == "color.png" and refs[1]["lastModified"] == 7
        assert (await db.get(Branch, branch)).state_version == 1
        assert (await db.get(Wallet, owner)).balance == 4

async def test_unified_reference_cannot_attach_another_owners_asset(factory, tmp_path):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    _, _, _, foreign = await fixture(factory, tmp_path)
    payload = request(branch, revision, root).model_dump(mode="json")
    payload["document"]["schema_version"] = 2
    payload["document"]["content"]["localReferences"] = [{"key":"private","assetId":foreign.id,"usage":"test"}]
    with pytest.raises(ApplicationError, match="not_found"):
        async with factory.begin() as db:
            await save_draft(db, owner, str(uuid4()), SaveDraft.model_validate(payload))

@pytest.mark.parametrize("case", ["duplicate_key","missing_file_time","path","too_many","mixed"])
def test_unified_reference_schema_rejects_ambiguous_or_invalid_content(case):
    from app.design.drafts import DraftDocument
    content = {"recipe":{},"maskStrokes":[],"localEditNote":"","selectedLocalReferenceKeys":[],
        "localUploadedReferenceDescription":"","extraReferenceDescription":"",
        "localReferences":[{"key":"ref","assetId":"asset","usage":""}]}
    if case == "duplicate_key":
        content["localReferences"] *= 2
    elif case == "missing_file_time":
        content["localReferences"][0]["fileName"] = "a.png"
    elif case == "path":
        content["localReferences"][0].update(fileName="../a.png",lastModified=0)
    elif case == "too_many":
        content["localReferences"] = [{"key":str(i),"assetId":"asset","usage":""} for i in range(21)]
    else:
        content["selectedLocalReferenceKeys"] = ["asset"]
    with pytest.raises(ValidationError):
        DraftDocument(schema_version=2,source_sha256="a"*64,width=16,height=16,content=content,attachments=[])

@pytest.mark.parametrize("version, references", [(1, []), (2, None), (True, None), (2.0, []), (3, [])])
def test_draft_schema_version_cannot_hide_unified_references(version, references):
    from app.design.drafts import DraftDocument
    content = {"recipe":{},"maskStrokes":[],"localEditNote":"","selectedLocalReferenceKeys":[],
        "localUploadedReferenceDescription":"","extraReferenceDescription":"","localReferences":references}
    with pytest.raises(ValidationError):
        DraftDocument(schema_version=version,source_sha256="a"*64,width=16,height=16,content=content,attachments=[])

async def test_cloud_draft_cannot_be_downgraded_after_unified_reference_upgrade(factory, tmp_path):
    owner, branch, revision, root = await fixture(factory, tmp_path)
    old = request(branch, revision, root)
    upgraded = old.model_dump(mode="json")
    upgraded["document"]["schema_version"] = 2
    upgraded["document"]["content"]["localReferences"] = [{"key":"selected","assetId":root.id,"usage":"keep"}]
    key = str(uuid4())
    async with factory.begin() as db:
        await save_draft(db, owner, key, SaveDraft.model_validate(upgraded))
    downgraded = old.model_copy(update={"client_request_id":str(uuid4()),"expected_version":1})
    with pytest.raises(ApplicationError,match="draft_schema_conflict"):
        async with factory.begin() as db:
            await save_draft(db, owner, key, downgraded)
    async with factory() as db:
        assert (await db.get(EditorDraft,key)).document["schema_version"] == 2
