from __future__ import annotations
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.time import utc_now
from app.design.manual import ImageCoordinates
from app.design.models import Asset, EditorDraft, DraftWriteReceipt, Revision
from app.design.service import ApplicationError, digest, owned_branch

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class Point(StrictModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)

class MaskStroke(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    mode: Literal["brush", "erase"]
    points: list[Point] = Field(min_length=1, max_length=10000)
    radius: float = Field(gt=0, le=16384, allow_inf_nan=False)

class MaskBinding(StrictModel):
    sourceKey: str = Field(min_length=1, max_length=100)
    width: StrictInt = Field(gt=0, le=16384)
    height: StrictInt = Field(gt=0, le=16384)
    geometry: str = Field(max_length=1024 * 1024)

class DraftReference(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    assetId: str = Field(min_length=1, max_length=36)
    usage: str = Field(max_length=2000)
    fileName: str | None = Field(default=None, min_length=1, max_length=200)
    lastModified: StrictInt | None = Field(default=None, ge=0)
    @model_validator(mode="after")
    def file_metadata(self):
        if (self.fileName is None) != (self.lastModified is None):
            raise ValueError("Incomplete reference file metadata")
        if self.fileName and any(char in self.fileName for char in ("/", "\\", "\x00")):
            raise ValueError("Invalid file name")
        return self

class DraftContent(StrictModel):
    recipe: dict
    maskStrokes: list[MaskStroke] = Field(max_length=10000)
    maskBinding: MaskBinding | None = None
    localReferences: list[DraftReference] | None = Field(default=None, max_length=20)
    localEditMode: Literal["preserve", "lock_outside"] | None = None
    localEditNote: str = Field(max_length=10000)
    selectedLocalReferenceKeys: list[str] = Field(max_length=20)
    localUploadedReferenceDescription: str = Field(max_length=10000)
    extraReferenceDescription: str = Field(max_length=10000)

class Attachment(StrictModel):
    slot: Literal["local", "extra"]
    asset_id: str = Field(min_length=1, max_length=36)
    file_name: str = Field(min_length=1, max_length=200)
    last_modified: StrictInt = Field(ge=0)
    @model_validator(mode="after")
    def filename(self):
        if any(char in self.file_name for char in ("/", "\\", "\x00")):
            raise ValueError("Invalid file name")
        return self

class DraftDocument(StrictModel):
    schema_version: StrictInt = Field(ge=1, le=2)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: StrictInt = Field(gt=0, le=16384)
    height: StrictInt = Field(gt=0, le=16384)
    content: DraftContent
    attachments: list[Attachment] = Field(max_length=2)
    @model_validator(mode="after")
    def valid_document(self):
        references = self.content.localReferences
        if (self.schema_version == 2) != (references is not None):
            raise ValueError("Unified references require draft schema 2")
        if references is not None and len({item.key for item in references}) != len(references):
            raise ValueError("Duplicate reference key")
        if references is not None and (self.content.selectedLocalReferenceKeys or self.content.localUploadedReferenceDescription or any(item.slot == "local" for item in self.attachments)):
            raise ValueError("Mixed legacy and unified local references")
        if len({item.slot for item in self.attachments}) != len(self.attachments):
            raise ValueError("Duplicate attachment slot")
        if len(json.dumps(self.model_dump(mode="json"), allow_nan=False).encode()) > 4 * 1024 * 1024:
            raise ValueError("Draft too large")
        if "imageCoordinates" in self.content.recipe:
            coords = ImageCoordinates.model_validate(self.content.recipe["imageCoordinates"])
            if (coords.width, coords.height) != (self.width, self.height):
                raise ValueError("Recipe size mismatch")
        binding = self.content.maskBinding
        if binding and (binding.width, binding.height) != (self.width, self.height):
            raise ValueError("Mask size mismatch")
        return self

class SaveDraft(StrictModel):
    client_request_id: str = Field(min_length=8, max_length=100)
    expected_version: StrictInt = Field(ge=0)
    device_id: str = Field(min_length=1, max_length=100)
    branch_id: str = Field(min_length=1, max_length=36)
    base_revision_id: str = Field(min_length=1, max_length=36)
    root_asset_id: str = Field(min_length=1, max_length=36)
    document: DraftDocument

class DeleteDraft(StrictModel):
    client_request_id: str = Field(min_length=8, max_length=100)
    expected_version: StrictInt = Field(ge=1)

def receipt_json(receipt: DraftWriteReceipt):
    return {"id": receipt.draft_id, "version": receipt.result_version, "deleted": receipt.deleted}

def draft_json(record: EditorDraft):
    return {"id": record.id, "version": record.version, "branch_id": record.branch_id,
        "base_revision_id": record.base_revision_id, "root_asset_id": record.root_asset_id,
        "document": record.document, "device_id": record.device_id, "updated_at": record.updated_at,
        "root_image_url": f"/api/v2/assets/{record.root_asset_id}"}

async def save_draft(db: AsyncSession, owner: str, draft_id: str, request: SaveDraft):
    design, _ = await owned_branch(db, owner, request.branch_id, lock=True)
    if design.archived_at:
        raise ApplicationError("design_archived")
    current = await db.get(EditorDraft, draft_id)
    if current and (current.owner_id != owner or current.branch_id != request.branch_id or
        current.base_revision_id != request.base_revision_id or current.root_asset_id != request.root_asset_id):
        raise ApplicationError("not_found")
    payload = request.model_dump(mode="json")
    for field in ("localEditMode", "localReferences"):
        if getattr(request.document.content, field) is None:
            payload["document"]["content"].pop(field)  # Keep existing receipt digests stable.
    fingerprint = digest({"method": "PUT", "payload": payload})
    previous = await db.get(DraftWriteReceipt, (draft_id, request.client_request_id)) if current else None
    if previous:
        if previous.request_digest != fingerprint:
            raise ApplicationError("idempotency_conflict")
        return receipt_json(previous)
    if (current and (current.deleted_at or current.version != request.expected_version)) or (not current and request.expected_version != 0):
        raise ApplicationError("draft_conflict")
    if current and current.document.get("schema_version") == 2 and request.document.schema_version == 1:
        raise ApplicationError("draft_schema_conflict")
    base = await db.get(Revision, request.base_revision_id)
    if not base or base.design_id != design.id or base.root_source_asset_id != request.root_asset_id:
        raise ApplicationError("not_found")
    asset_ids = {request.root_asset_id, *request.document.content.selectedLocalReferenceKeys,
                 *(item.assetId for item in request.document.content.localReferences or []),
                 *(item.asset_id for item in request.document.attachments)}
    assets = (await db.execute(select(Asset).where(Asset.id.in_(asset_ids), Asset.owner_id == owner, Asset.status == "ready"))).scalars().all()
    if len(assets) != len(asset_ids):
        raise ApplicationError("not_found")
    root = next(item for item in assets if item.id == request.root_asset_id)
    if (root.sha256, root.width, root.height) != (request.document.source_sha256, request.document.width, request.document.height):
        raise ApplicationError("draft_source_mismatch")
    binding = request.document.content.maskBinding
    if binding and binding.sourceKey != root.id:
        raise ApplicationError("draft_source_mismatch")
    if not current:
        current = EditorDraft(id=draft_id, owner_id=owner, design_id=design.id, branch_id=request.branch_id,
            base_revision_id=base.id, root_asset_id=root.id, version=0)
        db.add(current)
    current.version += 1
    current.document = payload["document"]
    current.device_id = request.device_id
    current.updated_at = utc_now()
    await db.flush()
    receipt = DraftWriteReceipt(draft_id=draft_id, request_id=request.client_request_id,
        request_digest=fingerprint, result_version=current.version, deleted=False)
    db.add(receipt)
    # Draft autosave deliberately does not move current_revision, design recency, events, or wallet.
    await db.flush()
    return receipt_json(receipt)

async def list_drafts(db: AsyncSession, owner: str, branch_id: str, base_revision_id: str):
    design, _ = await owned_branch(db, owner, branch_id)
    base = await db.get(Revision, base_revision_id)
    if not base or base.design_id != design.id:
        raise ApplicationError("not_found")
    records = (await db.execute(select(EditorDraft).where(EditorDraft.owner_id == owner,
        EditorDraft.branch_id == branch_id, EditorDraft.base_revision_id == base.id, EditorDraft.deleted_at.is_(None))
        .order_by(EditorDraft.updated_at.desc(), EditorDraft.id))).scalars()
    return [draft_json(record) for record in records]

async def delete_draft(db: AsyncSession, owner: str, draft_id: str, request: DeleteDraft):
    record = await db.get(EditorDraft, draft_id)
    if not record or record.owner_id != owner:
        raise ApplicationError("not_found")
    await owned_branch(db, owner, record.branch_id, lock=True)
    await db.refresh(record)
    fingerprint = digest({"method": "DELETE", "payload": request.model_dump(mode="json")})
    previous = await db.get(DraftWriteReceipt, (draft_id, request.client_request_id))
    if previous:
        if previous.request_digest != fingerprint:
            raise ApplicationError("idempotency_conflict")
        return receipt_json(previous)
    if record.deleted_at or record.version != request.expected_version:
        raise ApplicationError("draft_conflict")
    record.version += 1
    record.deleted_at = utc_now()
    record.updated_at = record.deleted_at
    # A deleted cloud draft has no restore operation. Keep only its tombstone and
    # receipts; do not retain private notes, masks or attachment references forever.
    record.document = {}
    record.device_id = ""
    receipt = DraftWriteReceipt(draft_id=draft_id, request_id=request.client_request_id,
        request_digest=fingerprint, result_version=record.version, deleted=True)
    db.add(receipt)
    await db.flush()
    return receipt_json(receipt)
