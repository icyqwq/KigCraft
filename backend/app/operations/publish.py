from __future__ import annotations
from app.assets.coordination import storage_writer
from app.assets.repository import asset_store, storage_io
import hashlib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.codex_runtime.artifacts import StagedImage
from app.contracts.v2 import InputSnapshot
from app.design.models import Asset, Branch, CodexEpoch, InputRecord, Operation, Quote, Revision
from app.conversation.checkpoints import checkpoint
from app.design.service import ApplicationError, event
from app.images.encode import MIME, SUFFIX
from app.operations.image_kind import resolve_image_kind
from app.operations.leases import check_fence, locked_operation
from app.quota.wallet import release, settle


def blob_format(staged: StagedImage) -> tuple[str, str]:
    """(suffix, mime) of a staged picture. Generated results are JPEG; a pixel-locked local edit stays lossless PNG."""
    return (".png", "image/png") if staged.path.suffix == ".png" else (SUFFIX, MIME)


def install_blob(staged: StagedImage, storage_root: Path, asset_id: str) -> str:
    """Write/verify private object first. Failed DB commits leave retryable unreferenced objects."""
    data = staged.path.read_bytes()
    if hashlib.sha256(data).hexdigest() != staged.sha256:
        raise ApplicationError("staged_image_changed")
    suffix, mime = blob_format(staged)
    key = f"masters/{asset_id}{suffix}"
    asset_store(storage_root).put(key, data, staged.sha256, mime)
    return key


async def inline_delivered(db: AsyncSession, operation) -> set[str]:
    """Delivery keys already stored as bare assets (the editor's local results), which have no revision."""
    found = set()
    for key in (operation.staged_outputs or {}):
        if await db.get(Asset, str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{operation.id}:{key}"))) is not None:
            found.add(key)
    return found


async def publish_inline(db: AsyncSession, operation, delivery_key: str, staged: StagedImage, storage_root: Path,
                         finalize: bool) -> Asset:
    """The editor's local generation keeps only the picture: no revision, no version number, no change to any branch.

    The editor adopts the asset as its new working image; a version exists only once the user saves from the editor.
    """
    asset_id = str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{operation.id}:{delivery_key}"))
    existing = await db.get(Asset, asset_id)
    if existing is not None:
        if existing.sha256 != staged.sha256:
            raise ApplicationError("delivery_identity_conflict")
        return existing
    if operation.state not in {"running", "publishing"} or operation.reservation_closed:
        raise ApplicationError("operation_not_publishable")
    bound_quote = await db.get(Quote, operation.quote_id)
    if bound_quote is None or delivery_key not in bound_quote.deliverables:
        raise ApplicationError("unauthorized_delivery")
    units = bound_quote.deliverables[delivery_key]
    key = await storage_io(install_blob, staged, storage_root, asset_id)
    asset = Asset(id=asset_id, owner_id=operation.owner_id, object_key=key, sha256=staged.sha256,
                  mime_type=blob_format(staged)[1], kind="master", width=staged.width, height=staged.height,
                  byte_size=staged.path.stat().st_size, status="ready")
    db.add(asset)
    await db.flush()
    await settle(db, operation, delivery_key, units)
    if finalize:
        await release(db, operation)
        operation.state = "succeeded"
    else:
        operation.state = "publishing"
    await event(db, operation.design_id, "quota.changed", {"operation_id": operation.id})
    await event(db, operation.design_id, "operation.state_changed", {"operation_id": operation.id, "state": operation.state})
    return asset


async def publish(db: AsyncSession, operation_id: str, worker_id: str, token: int,
                  delivery_key: str, staged: StagedImage, *, storage_root: Path, finalize: bool = True) -> Revision:
    await storage_writer(db)
    operation = await locked_operation(db, operation_id)
    check_fence(operation, worker_id, token)
    record = await db.get(InputRecord, operation.input_snapshot_id)
    inputs = InputSnapshot.model_validate(record.snapshot)
    if inputs.local_edit_mode == "lock_outside" and (inputs.digest() != record.digest or staged.local_input_digest != record.digest):
        raise ApplicationError("unprocessed_local_output")
    if inputs.inline_local:
        return await publish_inline(db, operation, delivery_key, staged, storage_root, finalize)
    previous = (await db.execute(select(Revision).where(
        Revision.operation_id == operation.id, Revision.delivery_key == delivery_key
    ))).scalar_one_or_none()
    if previous is not None:
        asset = await db.get(Asset, previous.clean_asset_id)
        if asset is None or asset.sha256 != staged.sha256:
            raise ApplicationError("delivery_identity_conflict")
        return previous
    if operation.state not in {"running", "publishing"} or operation.reservation_closed:
        raise ApplicationError("operation_not_publishable")
    view_item = None
    if operation.kind == "viewset":
        from app.operations.viewset_group import authorize_publication
        view_item = await authorize_publication(db, operation)
        from app.operations.viewset_group import check_view_image
        await check_view_image(db, operation, staged.sha256)
        finalize = False
    bound_quote = await db.get(Quote, operation.quote_id)
    if bound_quote is None or delivery_key not in bound_quote.deliverables:
        raise ApplicationError("unauthorized_delivery")
    units = bound_quote.deliverables[delivery_key]
    asset_id = str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{operation.id}:{delivery_key}"))
    key = await storage_io(install_blob, staged, storage_root, asset_id)
    asset = Asset(id=asset_id, owner_id=operation.owner_id, object_key=key, sha256=staged.sha256,
                  mime_type=blob_format(staged)[1], kind="master", width=staged.width, height=staged.height,
                  byte_size=staged.path.stat().st_size, status="ready")
    db.add(asset)
    await db.flush()
    bound_branch = await db.get(Branch, operation.branch_id)
    epoch = await db.get(CodexEpoch, bound_branch.active_epoch_id) if bound_branch.active_epoch_id else None
    context = (checkpoint(epoch, operation.upstream_turn_id)
               if epoch and epoch.thread_id == operation.upstream_thread_id and operation.upstream_turn_id else None)
    # Regeneration targets the selected result's view, even when its original input was a front.
    view_source_id = bound_branch.origin_revision_id if operation.kind == "regenerate" else operation.base_revision_id
    view_source = await db.get(Revision, view_source_id) if view_source_id else None
    if view_source_id and (view_source is None or view_source.design_id != operation.design_id):
        raise ApplicationError("input_asset_unavailable")
    # A four-view sheet is a derived result: it is kept in the conversation but never becomes the working base.
    sheet = inputs.target_kind == "four_view"
    revision = Revision(design_id=operation.design_id, branch_id=operation.branch_id,
                        parent_revision_id=operation.base_revision_id, clean_asset_id=asset.id,
                        root_source_asset_id=asset.id, operation_id=operation.id,
                        delivery_key=delivery_key, source_kind="ai", image_kind=await resolve_image_kind(db, operation, view_source), view=view_item.view if view_item else "sheet" if sheet else view_source.view if view_source else "front",
                        context_checkpoint=context)
    db.add(revision)
    await db.flush()
    branch = (await db.execute(select(Branch).where(Branch.id == operation.branch_id).with_for_update())).scalar_one()
    is_current = (not sheet and branch.state_version == operation.expected_branch_version
                  and (operation.base_revision_id is None or branch.current_revision_id == operation.base_revision_id))
    if is_current:
        branch.current_revision_id = revision.id
        branch.state_version += 1
    if view_item is not None:
        view_item.revision_id = revision.id
        view_item.state = "delivered"
        view_item.error_code = None
    await settle(db, operation, delivery_key, units)
    delivered = set((await db.execute(select(Revision.delivery_key).where(
        Revision.operation_id == operation.id
    ))).scalars())
    if finalize and set(bound_quote.deliverables) <= delivered:
        await release(db, operation)
        operation.state = "succeeded"
    else:
        operation.state = "publishing"
    await event(db, operation.design_id, "image.ready", {
        "operation_id": operation.id, "revision_id": revision.id,
        "asset_id": asset.id, "is_current": is_current,
    })
    if is_current:
        await event(db, operation.design_id, "revision.current_changed", {
            "branch_id": branch.id, "revision_id": revision.id, "state_version": branch.state_version,
        })
    await event(db, operation.design_id, "quota.changed", {"operation_id": operation.id})
    await event(db, operation.design_id, "operation.state_changed", {
        "operation_id": operation.id, "state": operation.state,
    })
    return revision
