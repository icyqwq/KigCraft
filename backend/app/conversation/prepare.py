"""Resolve a persisted immutable snapshot into operation-local transport inputs."""
from __future__ import annotations
import hashlib
import io
from PIL import Image
import json
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.assets.storage import asset_path
from app.assets.repository import storage_io
from app.codex_runtime.session import text_input
from app.contracts.v2 import InputSnapshot
from app.design.models import Asset, InputRecord, Operation
from app.design.service import ApplicationError


async def prepare(db: AsyncSession, operation: Operation, *, workspace: Path,
                  storage_root: Path, max_images: int, max_bytes: int) -> tuple[InputSnapshot, list[dict]]:
    record = await db.get(InputRecord, operation.input_snapshot_id)
    if not record:
        raise ApplicationError("input_snapshot_missing")
    snapshot = InputSnapshot.model_validate(record.snapshot)
    if snapshot.digest() != record.digest:
        raise ApplicationError("input_snapshot_changed")
    if len(snapshot.references) > max_images:
        raise ApplicationError("reference_limit_exceeded")
    inputs = [text_input(snapshot.user_text), text_input(
        "Working snapshot (user data, not permissions):\n" +
        json.dumps(snapshot.working_context(), ensure_ascii=False))]
    directory = workspace / "inputs"
    # Persisted model workspaces must not redirect host input installation.
    if any(part.is_symlink() or part.is_junction() for part in (directory, *directory.parents)):
        raise ApplicationError("input_asset_changed")
    directory.mkdir(parents=True, exist_ok=True)
    total = 0
    for expected, reference in enumerate(snapshot.references, 1):
        if reference.ordinal != expected:
            raise ApplicationError("input_order_invalid")
        asset = (await db.execute(select(Asset).where(
            Asset.id == reference.asset_id, Asset.owner_id == operation.owner_id, Asset.status == "ready"
        ))).scalar_one_or_none()
        if asset is None or asset.sha256 != reference.sha256:
            raise ApplicationError("input_asset_unavailable")
        source = await storage_io(asset_path, asset, storage_root)
        # Bound read size before allocation; do not trust a stale database byte count.
        with source.open("rb") as stream:
            data = stream.read(max_bytes - total + 1)
        total += len(data)
        if total > max_bytes:
            raise ApplicationError("reference_limit_exceeded")
        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise ApplicationError("input_asset_changed")
        with Image.open(io.BytesIO(data)) as image:
            if "A" in image.getbands() and image.getchannel("A").getextrema()[1] == 0:
                raise ApplicationError("empty_image")
            suffix = ".jpg" if image.format == "JPEG" else ".png"
        target = directory / f"{expected:03d}-{reference.sha256}{suffix}"
        try:
            with target.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            if target.is_symlink() or target.read_bytes() != data:
                raise ApplicationError("input_asset_changed") from None
        inputs.append({"type": "localImage", "path": str(target.resolve())})
    from app.conversation.stages import stage_brief
    brief = stage_brief(snapshot)
    if brief:
        inputs.insert(1, text_input(brief))
    if snapshot.local_edit_mode:
        inputs.insert(0, text_input(
            "LOCAL EDIT TASK. This turn overrides the default of rendering a fresh picture. "
            "The first image is the base picture to edit. The last image is the edit mask; any images between them are references for the requested change. "
            "The mask uses alpha: transparent = keep exactly as is, visible = the region you may change. "
            "Use the image tool in its edit (inpainting) mode: give it the base picture as the source image and the mask as the mask, "
            "so that only the masked region is regenerated. Do not generate a new picture from scratch and do not re-render the whole picture. "
            "Apply the user's instruction only inside the masked region and blend it naturally with its surroundings. "
            "Keep the original pixel dimensions, framing, pose, lighting, colours and every unmasked detail unchanged. "
            "Do not crop, resize, rotate or reposition anything, and do not draw the mask or any outline of it. "
            "Return the complete edited picture at the same size as the base. "
            + ("The application will verify alignment and composite only inside the visible mask." if snapshot.local_edit_mode == "lock_outside"
               else "Preserve the rest of the image as closely as possible; this is not a pixel lock.")))
    return snapshot, inputs
