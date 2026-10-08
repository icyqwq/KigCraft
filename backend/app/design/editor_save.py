"""Save editor state without inventing a visible revision for identical pixels."""
from __future__ import annotations
import asyncio
from pathlib import Path
from pydantic import Field, StrictInt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.contracts.v2 import ContinuationDraft
from app.design.drafts import StrictModel
from app.design.editor_document import EditorDocument
from app.design.manual import SaveManualRevision, save_manual
from app.design.models import Asset, EditorCheckpoint, EditorSaveReceipt, Operation, Revision
from app.design.service import ApplicationError, digest, owned_branch
from app.operations.service import ACTIVE
from app.operations.local_edit import bytes_for
from app.operations.local_images import decode
from app.codex_runtime.artifacts import ArtifactError

class SaveEditorDocument(StrictModel):
    client_request_id: str = Field(min_length=8, max_length=100)
    expected_branch_version: StrictInt = Field(ge=0)
    expected_checkpoint_id: str | None = Field(default=None, min_length=1, max_length=36)
    clean_asset_id: str = Field(min_length=1, max_length=36)
    document: EditorDocument
    continuation: ContinuationDraft | None = None

async def latest_checkpoint(db, branch_id, revision_id):
    return (await db.execute(select(EditorCheckpoint).where(
        EditorCheckpoint.branch_id == branch_id, EditorCheckpoint.revision_id == revision_id
    ).order_by(EditorCheckpoint.version.desc()).limit(1))).scalar_one_or_none()

def checkpoint_json(item):
    return None if item is None else {"id":item.id,"version":item.version,"branch_id":item.branch_id,
        "revision_id":item.revision_id,"clean_asset_id":item.clean_asset_id,"document":item.document,
        "created_at":item.created_at}

async def read_checkpoint(db, owner, branch_id, revision_id):
    design, _ = await owned_branch(db, owner, branch_id)
    revision = await db.get(Revision, revision_id)
    if not revision or revision.design_id != design.id:
        raise ApplicationError("not_found")
    return await latest_checkpoint(db, branch_id, revision_id)

def same_pixels(base, candidate, root, storage_root):
    # Verify files against the immutable identities before comparing decoded pixels.
    images = {}
    try:
        for asset in (root, base, candidate):
            if asset.id not in images:
                image = decode(bytes_for(asset, storage_root))
                if image.size != (asset.width, asset.height):
                    raise ApplicationError("input_asset_changed")
                images[asset.id] = image
        first, second = images[base.id], images[candidate.id]
        if second.convert("RGBA").getchannel("A").getextrema()[1] == 0:
            raise ApplicationError("empty_image")
        return first.size == second.size and first.info.get("icc_profile") == second.info.get("icc_profile") and first.convert("RGBA").tobytes() == second.convert("RGBA").tobytes()
    except ArtifactError:
        raise ApplicationError("invalid_image") from None
    finally:
        for image in images.values():
            image.close()

async def save_editor(db: AsyncSession, owner: str, branch_id: str, payload: SaveEditorDocument, *, storage_root: Path):
    design, branch = await owned_branch(db, owner, branch_id, lock=True)
    if design.archived_at:
        raise ApplicationError("design_archived")
    fingerprint = digest({"branch_id":branch_id,"request":payload.model_dump(mode="json")})
    previous = await db.get(EditorSaveReceipt, (design.id, payload.client_request_id))
    if previous:
        if previous.request_digest != fingerprint:
            raise ApplicationError("idempotency_conflict")
        saved = await db.get(EditorCheckpoint, previous.checkpoint_id)
        return previous.disposition, saved, await db.get(Revision, saved.revision_id)
    if (await db.execute(select(Revision.id).where(Revision.design_id == design.id,
            Revision.client_request_id == payload.client_request_id))).first():
        raise ApplicationError("idempotency_conflict")
    document = payload.document
    parent = await db.get(Revision, document.baseRevision)
    if not parent or parent.design_id != design.id:
        raise ApplicationError("not_found")
    if payload.continuation and (payload.continuation.source_revision_id != parent.id or payload.continuation.boundary_message_id):
        raise ApplicationError("state_conflict")
    if branch.state_version != payload.expected_branch_version or (branch.current_revision_id != parent.id and not payload.continuation):
        raise ApplicationError("state_conflict")
    if (await db.execute(select(Operation.id).where(Operation.design_id == design.id, Operation.state.in_(ACTIVE)))).first():
        raise ApplicationError("design_busy")
    current = await latest_checkpoint(db, branch.id, parent.id)
    if (current.id if current else None) != payload.expected_checkpoint_id:
        raise ApplicationError("editor_checkpoint_conflict")
    if document.root.assetId != parent.root_source_asset_id:
        # The editor may have adopted the result of a local generation as its working image.
        from app.operations.inline_results import inline_result
        if await inline_result(db, owner, design.id, document.root.assetId) is None:
            raise ApplicationError("editor_source_mismatch")
    assets = {}
    for asset_id in {parent.clean_asset_id, parent.root_source_asset_id, payload.clean_asset_id, document.root.assetId}:
        asset = await db.get(Asset, asset_id)
        if not asset or asset.owner_id != owner or asset.status != "ready":
            raise ApplicationError("not_found")
        assets[asset_id] = asset
    root, clean, original = assets[document.root.assetId], assets[payload.clean_asset_id], assets[parent.clean_asset_id]
    if (root.id, root.sha256, root.width, root.height) != (document.root.assetId, document.root.sha256, document.root.width, document.root.height):
        raise ApplicationError("editor_source_mismatch")
    if (clean.width, clean.height) != (root.width, root.height):
        raise ApplicationError("recipe_image_mismatch")
    if current and current.document.get("root", {}).get("assetId") == document.root.assetId and current.document["normalizedRecipe"].get("landmarks") != document.normalizedRecipe.get("landmarks") and current.document["calibrationRevision"] == document.calibrationRevision:
        raise ApplicationError("calibration_revision_required")
    identical = await asyncio.to_thread(same_pixels, original, clean, root, storage_root)
    result = parent
    if not identical:
        result = await save_manual(db, owner, branch_id, SaveManualRevision(
            client_request_id=payload.client_request_id, base_revision_id=parent.id,
            expected_branch_version=payload.expected_branch_version, clean_asset_id=clean.id,
            recipe=document.legacy_recipe(), continuation=payload.continuation,
            root_asset_id=root.id if root.id != parent.root_source_asset_id else None))
        current = None  # The new image gets its own document history.
    final_document = document.model_copy(update={"baseRevision": result.id}).model_dump(mode="json")
    document_digest = digest(final_document)
    if current and current.document_digest == document_digest:
        saved, disposition = current, "unchanged"
    else:
        saved = EditorCheckpoint(owner_id=owner, design_id=design.id, branch_id=result.branch_id if not identical else branch.id,
            revision_id=result.id, root_asset_id=root.id, clean_asset_id=result.clean_asset_id,
            version=(current.version + 1) if current else 1, document=final_document, document_digest=document_digest)
        db.add(saved)
        await db.flush()
        disposition = "checkpoint" if identical else "revision"
    # Checkpoints do not move current, event sequence, recency or the wallet. Visible revisions use the existing CAS path.
    db.add(EditorSaveReceipt(design_id=design.id, request_id=payload.client_request_id,
        request_digest=fingerprint, checkpoint_id=saved.id, disposition=disposition))
    await db.flush()
    return disposition, saved, result
