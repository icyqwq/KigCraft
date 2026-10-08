"""Validate local input before quoting and process locked output before publication/settlement."""
from __future__ import annotations
import asyncio
import hashlib
from pathlib import Path
from sqlalchemy import select
from app.assets.storage import asset_path, MAX_UPLOAD_BYTES
from app.codex_runtime.artifacts import ArtifactError, StagedImage
from app.contracts.v2 import InputSnapshot, PreparedEdit
from app.design.manual import ImageCoordinates
from app.design.models import Asset, InputRecord
from app.design.service import ApplicationError
from app.operations.local_images import anchor_area, composite_locked, conform_to_base, decode, validate_pair

def bytes_for(asset: Asset, root: Path) -> bytes:
    with asset_path(asset, root).open("rb") as stream:
        data = stream.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApplicationError("image_too_large")
    if hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ApplicationError("input_asset_changed")
    return data

async def validate_prepared(db, owner: str, revision, prepared: PreparedEdit):
    root = await db.get(Asset, revision.root_source_asset_id)
    if prepared.root_source_asset_id != revision.root_source_asset_id:
        # The editor may be working on the result of an earlier local generation instead of the revision's own root.
        from app.operations.inline_results import inline_result
        root = await inline_result(db, owner, revision.design_id, prepared.root_source_asset_id)
    clean = await db.get(Asset, prepared.clean_asset_id)
    if not root or not clean or root.owner_id != owner or clean.owner_id != owner or root.status != "ready" or clean.status != "ready":
        raise ApplicationError("not_found")
    if (root.id, root.sha256, root.width, root.height) != (prepared.root_source_asset_id, prepared.root_sha256, prepared.width, prepared.height):
        raise ApplicationError("prepared_source_mismatch")
    if (clean.width, clean.height) != (root.width, root.height):
        raise ApplicationError("prepared_source_mismatch")
    try:
        coordinates = ImageCoordinates.model_validate(prepared.recipe.get("imageCoordinates"))
    except ValueError:
        raise ApplicationError("invalid_recipe") from None
    if (coordinates.width, coordinates.height) != (root.width, root.height):
        raise ApplicationError("prepared_source_mismatch")

async def pair(db, owner: str, snapshot: InputSnapshot, storage_root: Path):
    references = {item.role: item for item in snapshot.references if item.role in {"base", "mask"}}
    if set(references) != {"base", "mask"}:
        raise ApplicationError("local_input_missing")
    result = {}
    for role, reference in references.items():
        asset = (await db.execute(select(Asset).where(Asset.id == reference.asset_id,
            Asset.owner_id == owner, Asset.status == "ready", Asset.sha256 == reference.sha256))).scalar_one_or_none()
        if not asset:
            raise ApplicationError("input_asset_unavailable")
        result[role] = await asyncio.to_thread(bytes_for, asset, storage_root)
    return result["base"], result["mask"]

async def validate_local_input(db, owner: str, snapshot: InputSnapshot, storage_root: Path):
    base, mask = await pair(db, owner, snapshot, storage_root)
    try:
        image, selection = decode(base), decode(mask)
        alpha = validate_pair(image, selection)
        if snapshot.local_edit_mode == "lock_outside":
            anchor_area(image, alpha)
    except ArtifactError as error:
        raise ApplicationError(str(error)) from error

async def process_local_output(db, operation, staged: StagedImage, storage_root: Path, staging_root: Path):
    record = await db.get(InputRecord, operation.input_snapshot_id)
    if not record:
        raise ApplicationError("input_snapshot_missing")
    snapshot = InputSnapshot.model_validate(record.snapshot)
    if snapshot.digest() != record.digest:
        raise ApplicationError("input_snapshot_changed")
    if snapshot.local_edit_mode != "lock_outside":
        if not snapshot.inline_local:
            return staged
        base, _ = await pair(db, operation.owner_id, snapshot, storage_root)
        return await asyncio.to_thread(conform_to_base, base, staged, staging_root, record.digest)
    base, mask = await pair(db, operation.owner_id, snapshot, storage_root)
    return await asyncio.to_thread(composite_locked, base, mask, staged, staging_root, record.digest)
