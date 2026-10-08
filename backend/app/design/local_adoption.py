"""Explicitly copy a local editor snapshot into a new owned design, without running AI."""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Literal
from uuid import uuid4
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import User
from app.codex_runtime.artifacts import ArtifactError
from app.design.drafts import DraftDocument, SaveDraft, StrictModel, save_draft
from app.design.editor_document import InitialEditorDocument, EditorDocument
from app.design.editor_save import same_pixels
from app.design.models import Asset, Branch, Design, EditorCheckpoint, EditorSaveReceipt, Revision
from app.design.service import ApplicationError, digest, event
from app.operations.local_edit import bytes_for
from app.operations.local_images import decode


class AdoptLocalImage(StrictModel):
    client_request_id: str = Field(min_length=8, max_length=100)
    expected_owner_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=200)
    view: Literal["front", "left_three_quarter", "left_profile", "right_three_quarter", "right_profile", "back", "unknown"]
    clean_asset_id: str = Field(min_length=1, max_length=36)
    document: InitialEditorDocument
    draft: DraftDocument

    @model_validator(mode="after")
    def coherent_draft(self):
        if not self.title.strip():
            raise ValueError("Empty title")
        root, content = self.document.root, self.draft.content
        if (root.sha256, root.width, root.height) != (self.draft.source_sha256, self.draft.width, self.draft.height):
            raise ValueError("Draft source mismatch")
        if content.recipe != self.document.legacy_recipe():
            raise ValueError("Draft recipe mismatch")
        if content.maskStrokes != self.document.maskDocument.strokes or content.maskBinding != self.document.maskDocument.binding:
            raise ValueError("Draft mask mismatch")
        return self


def verify_assets(assets, root, clean, storage_root):
    # The initial revision uses the existing verified clean image, without baking the recipe again.
    same_pixels(root, clean, root, storage_root)
    for asset in assets.values():
        if asset.id in {root.id, clean.id}:
            continue
        try:
            image = decode(bytes_for(asset, storage_root))
        except ArtifactError as error:
            raise ApplicationError("invalid_image") from error
        try:
            if image.size != (asset.width, asset.height):
                raise ApplicationError("input_asset_changed")
        finally:
            image.close()


async def adopt_local(db: AsyncSession, owner: str, payload: AdoptLocalImage, *, storage_root: Path):
    if payload.expected_owner_id != owner:
        raise ApplicationError("not_found")
    # Same lock and request namespace as ordinary design creation: retries cannot race each other.
    user = await db.scalar(select(User).where(User.id == owner).with_for_update())
    if user is None or user.is_banned:
        raise ApplicationError("not_found")
    fingerprint = digest({"kind": "local-adoption-v1", "request": payload.model_dump(mode="json")})
    previous = await db.scalar(select(Design).where(
        Design.owner_id == owner, Design.client_request_id == payload.client_request_id))
    if previous:
        if previous.request_digest != fingerprint:
            raise ApplicationError("idempotency_conflict")
        # Replaying a receipt must not unarchive, restore, move current, or replay the draft.
        receipt = await db.get(EditorSaveReceipt, (previous.id, payload.client_request_id))
        if not receipt:
            raise ApplicationError("adoption_receipt_unavailable")
        checkpoint = await db.get(EditorCheckpoint, receipt.checkpoint_id)
        revision = await db.get(Revision, checkpoint.revision_id)
        return previous, checkpoint, revision

    root_id = payload.document.root.assetId
    content = payload.draft.content
    asset_ids = {root_id, payload.clean_asset_id, *content.selectedLocalReferenceKeys,
        *(item.assetId for item in content.localReferences or []),
        *(item.asset_id for item in payload.draft.attachments)}
    values = (await db.scalars(select(Asset).where(
        Asset.id.in_(asset_ids), Asset.owner_id == owner, Asset.status == "ready"))).all()
    if len(values) != len(asset_ids):
        raise ApplicationError("not_found")
    assets = {item.id: item for item in values}
    root, clean = assets[root_id], assets[payload.clean_asset_id]
    if (root.sha256, root.width, root.height) != (
            payload.document.root.sha256, payload.document.root.width, payload.document.root.height):
        raise ApplicationError("editor_source_mismatch")
    if (root.width, root.height) != (clean.width, clean.height):
        raise ApplicationError("recipe_image_mismatch")
    await asyncio.to_thread(verify_assets, assets, root, clean, storage_root)

    design = Design(owner_id=owner, client_request_id=payload.client_request_id,
        request_digest=fingerprint, title=payload.title)
    db.add(design)
    await db.flush()
    branch = Branch(design_id=design.id, context_seed={"kind": "fresh"})
    db.add(branch)
    await db.flush()
    revision = Revision(design_id=design.id, branch_id=branch.id,
        clean_asset_id=clean.id, root_source_asset_id=root.id, source_kind="local_import",
        view=payload.view, recipe=payload.document.legacy_recipe(), context_checkpoint={"kind": "fresh"},
        client_request_id=payload.client_request_id, request_digest=fingerprint)
    db.add(revision)
    await db.flush()
    document = EditorDocument.model_validate({
        **payload.document.model_dump(mode="json"), "baseRevision": revision.id}).model_dump(mode="json")
    saved = EditorCheckpoint(owner_id=owner, design_id=design.id, branch_id=branch.id,
        revision_id=revision.id, root_asset_id=root.id, clean_asset_id=clean.id,
        version=1, document=document, document_digest=digest(document))
    db.add(saved)
    await db.flush()
    db.add(EditorSaveReceipt(design_id=design.id, request_id=payload.client_request_id,
        request_digest=fingerprint, checkpoint_id=saved.id, disposition="revision"))
    design.last_branch_id = branch.id
    branch.current_revision_id = revision.id
    branch.state_version = 1
    await save_draft(db, owner, str(uuid4()), SaveDraft(
        client_request_id=payload.client_request_id, expected_version=0, device_id="local-adoption",
        branch_id=branch.id, base_revision_id=revision.id, root_asset_id=root.id, document=payload.draft))
    await event(db, design.id, "session.updated", {"design_id": design.id})
    await event(db, design.id, "image.ready", {"revision_id": revision.id, "asset_id": clean.id,
        "operation_id": None, "is_current": True})
    await event(db, design.id, "revision.current_changed", {"branch_id": branch.id,
        "revision_id": revision.id, "state_version": branch.state_version})
    await db.flush()
    return design, saved, revision
