"""Pictures made by the editor's own local generation: a bare asset with no revision, adopted as the editor's working image."""
from __future__ import annotations
from uuid import NAMESPACE_URL, uuid5
from sqlalchemy import select
from app.design.models import Asset, Operation


async def inline_result(db, owner: str, design_id: str, asset_id: str) -> Asset | None:
    """The asset, if it is a local-generation result of this design that belongs to the owner."""
    asset = await db.get(Asset, asset_id)
    if asset is None or asset.owner_id != owner or asset.status != "ready" or asset.kind != "master":
        return None
    operation_ids = (await db.execute(select(Operation.id).where(
        Operation.design_id == design_id, Operation.owner_id == owner, Operation.kind == "local_edit"))).scalars()
    wanted = {str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{item}:main_image")) for item in operation_ids}
    return asset if asset_id in wanted else None
