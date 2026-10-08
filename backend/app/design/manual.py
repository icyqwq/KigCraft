from __future__ import annotations
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.design.models import Asset, Branch, CodexEpoch, Operation, Revision
from app.contracts.v2 import ContinuationDraft
from app.design.continuation import continuation_plan
from app.conversation.checkpoints import checkpoint
from app.design.service import ApplicationError, digest, event, owned_branch


class ImageCoordinates(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    width: StrictInt = Field(gt=0, le=16384)
    height: StrictInt = Field(gt=0, le=16384)
    effectScale: float = Field(gt=0, le=16384, allow_inf_nan=False)


class SaveManualRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=8, max_length=100)
    base_revision_id: str = Field(min_length=1, max_length=100)
    expected_branch_version: StrictInt = Field(ge=0)
    clean_asset_id: str = Field(min_length=1, max_length=100)
    recipe: dict
    continuation: ContinuationDraft | None = None
    # Set when the editor replaced its working image with a local-generation result: the revision then starts from it.
    root_asset_id: str | None = Field(default=None, min_length=1, max_length=100)


async def save_manual(db: AsyncSession, owner: str, branch_id: str, payload: SaveManualRevision):
    design, branch = await owned_branch(db, owner, branch_id, lock=True)
    if design.archived_at:
        raise ApplicationError("design_archived")
    try:
        if len(json.dumps(payload.recipe, allow_nan=False).encode()) > 1024 * 1024:
            raise ValueError()
    except (ValueError, TypeError, RecursionError):
        raise ApplicationError("invalid_recipe") from None
    fingerprint = digest({"branch_id":branch_id,"request":payload.model_dump(mode="json")})
    previous = (await db.execute(select(Revision).where(
        Revision.design_id == design.id, Revision.client_request_id == payload.client_request_id
    ))).scalar_one_or_none()
    if previous:
        if previous.request_digest and previous.request_digest != fingerprint:
            raise ApplicationError("idempotency_conflict")
        if (previous.parent_revision_id, previous.clean_asset_id, previous.recipe) != (
                payload.base_revision_id, payload.clean_asset_id, payload.recipe):
            raise ApplicationError("idempotency_conflict")
        return previous
    prepared = None
    if payload.continuation:
        if payload.continuation.source_revision_id != payload.base_revision_id or payload.continuation.boundary_message_id:
            raise ApplicationError("state_conflict")
        prepared = await continuation_plan(db,owner,branch_id,payload.base_revision_id,payload.expected_branch_version)
    if branch.state_version != payload.expected_branch_version or (not prepared and branch.current_revision_id != payload.base_revision_id):
        raise ApplicationError("state_conflict")
    parent = (await db.execute(select(Revision).where(
        Revision.id == payload.base_revision_id, Revision.design_id == design.id
    ))).scalar_one_or_none()
    asset = (await db.execute(select(Asset).where(
        Asset.id == payload.clean_asset_id, Asset.owner_id == owner, Asset.status == "ready"
    ))).scalar_one_or_none()
    if not parent or not asset:
        raise ApplicationError("not_found")
    root_asset = None
    if "imageCoordinates" in payload.recipe:
        try:
            coordinates = ImageCoordinates.model_validate(payload.recipe["imageCoordinates"])
        except ValidationError:
            raise ApplicationError("invalid_recipe") from None
        root = await db.get(Asset, parent.root_source_asset_id)
        if payload.root_asset_id and payload.root_asset_id != parent.root_source_asset_id:
            from app.operations.inline_results import inline_result
            root = await inline_result(db, owner, design.id, payload.root_asset_id)
        if not root or root.owner_id != owner or root.status != "ready":
            raise ApplicationError("not_found")
        if root.id != parent.root_source_asset_id:
            root_asset = root
        expected_size = (coordinates.width, coordinates.height)
        if expected_size != (root.width, root.height) or expected_size != (asset.width, asset.height):
            raise ApplicationError("recipe_image_mismatch")
    if prepared and branch.current_revision_id != payload.base_revision_id:
        from app.operations.service import ACTIVE
        if (await db.execute(select(Operation.id).where(Operation.design_id==design.id,Operation.state.in_(ACTIVE)))).first():
            raise ApplicationError("design_busy")
        from app.design.history import fork_history_sequence
        history_end = await fork_history_sequence(db, parent)
        branch = Branch(design_id=design.id,parent_branch_id=parent.branch_id,
            origin_revision_id=parent.id,current_revision_id=parent.id,context_seed=parent.context_checkpoint,
            history_parent_sequence=history_end)
        db.add(branch)
        await db.flush()
    epoch = await db.get(CodexEpoch, branch.active_epoch_id) if branch.active_epoch_id else None
    context = (checkpoint(epoch, epoch.checkpoint_turn_id) if epoch and epoch.status == "ready"
               else parent.context_checkpoint)
    # Render from this original plus the complete recipe on reopening; never reapply to the baked clean image.
    revision = Revision(design_id=design.id, branch_id=branch.id, parent_revision_id=parent.id,
        clean_asset_id=asset.id, root_source_asset_id=root_asset.id if root_asset else parent.root_source_asset_id, source_kind="manual", view=parent.view, image_kind=parent.image_kind,
        client_request_id=payload.client_request_id, recipe=payload.recipe, context_checkpoint=context, request_digest=fingerprint)
    db.add(revision)
    await db.flush()
    design.last_branch_id = branch.id
    branch.current_revision_id = revision.id
    branch.state_version += 1
    await event(db, design.id, "image.ready", {"revision_id": revision.id, "asset_id": asset.id,
                                             "operation_id": None, "is_current": True})
    await event(db, design.id, "revision.current_changed", {"branch_id": branch.id,
                "revision_id": revision.id, "state_version": branch.state_version})
    return revision
