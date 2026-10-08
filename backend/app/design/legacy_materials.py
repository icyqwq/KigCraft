"""Owner-only catalog; raw legacy drafts require an explicit separate read."""
from sqlalchemy import select
from app.design.models import AccountContentRetirement, Asset, LegacyMaterial
from app.design.service import ApplicationError


async def list_materials(db, owner_id, *, cursor=None, limit=50, kind="reference"):
    query = select(LegacyMaterial).where(LegacyMaterial.owner_id == owner_id,
        LegacyMaterial.owner_id.not_in(select(AccountContentRetirement.owner_id)))
    if kind == "reference":
        query = query.where(LegacyMaterial.source_kind != "draft")
    elif kind == "draft":
        query = query.where(LegacyMaterial.source_kind == "draft")
    if cursor:
        query = query.where(LegacyMaterial.id > cursor)
    rows = list((await db.scalars(query.order_by(LegacyMaterial.id).limit(limit + 1))).all())
    selected = rows[:limit]
    assets = {row.id: row for row in (await db.scalars(select(Asset).where(
        Asset.owner_id == owner_id, Asset.id.in_([row.asset_id for row in selected if row.asset_id])))).all()}
    items = []
    for row in selected:
        record = row.source_summary["record"]
        project = row.source_summary.get("project")
        asset = assets.get(row.asset_id)
        available = asset is not None and asset.status == "ready"
        items.append({"id": row.id, "source_kind": row.source_kind,
            "file_name": record.get("file_name"), "legacy_kind": record.get("kind"),
            "project_name": project["name"] if project else None,
            "asset_id": row.asset_id if available else None,
            "image_url": "/api/v2/assets/" + row.asset_id if available else None,
            "width": asset.width if available else None, "height": asset.height if available else None,
            "draft_preserved": row.source_kind == "draft", "editor_document": False,
            "issues": row.source_summary.get("issues", [])})
    return {"items": items, "next_cursor": selected[-1].id if len(rows) > limit else None}


async def preserved_draft(db, owner_id, material_id):
    row = await db.scalar(select(LegacyMaterial).where(
        LegacyMaterial.id == material_id, LegacyMaterial.owner_id == owner_id,
        LegacyMaterial.source_kind == "draft",
        LegacyMaterial.owner_id.not_in(select(AccountContentRetirement.owner_id))))
    if row is None:
        raise ApplicationError("not_found")
    return {"format": 1, "source_kind": "legacy_project_draft", "source_digest": row.source_digest,
            "editor_document": False, "replay": False, "record": row.source_summary["record"],
            "project": row.source_summary.get("project")}
