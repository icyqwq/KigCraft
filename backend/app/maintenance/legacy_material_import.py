"""Copy reviewed legacy materials without inferring draft schema or design ancestry."""
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from app.db.models import Project, ProjectDraft, ProjectReference, ReferenceAsset
from app.design.models import Asset, LegacyMaterial
from app.maintenance.legacy_import import install_original
from app.maintenance.legacy_materials import MIME_TYPES, reference_key, source_record
from app.maintenance.legacy_materials_archive import encoded
from app.maintenance.codex_snapshot import checked_path, SnapshotError
from app.maintenance.v2_migration import InventoryError
import hashlib


async def import_materials(db, owner_id, report, *, reference_root: Path | None, storage_root: Path):
    """Caller holds apply_inventory's source-table, owner and storage-writer locks."""
    from app.assets.repository import require_filesystem
    from app.core.config import get_settings
    require_filesystem(get_settings())
    collections = (("references", "reference", ReferenceAsset),
                   ("project_references", "project_reference", ProjectReference),
                   ("drafts", "draft", ProjectDraft))
    imported, replayed, skipped = 0, 0, []
    reference_count = draft_count = 0
    for collection, kind, model in collections:
        for item in report["ancillary"][collection]:
            identity = item["project_id"] if kind == "draft" else item["id"]
            source = await db.get(model, identity)
            if source is None:
                raise InventoryError("legacy_inventory_changed")
            old = await db.scalar(select(LegacyMaterial).where(
                LegacyMaterial.source_kind == kind, LegacyMaterial.source_id == identity))
            image = item.get("image")
            if old:
                if old.owner_id != owner_id or old.source_digest != item["source_digest"]:
                    raise InventoryError("legacy_material_changed_after_import")
                if old.asset_id:
                    asset = await db.get(Asset, old.asset_id)
                    if (not image or image.get("state") != "available" or asset is None
                            or asset.owner_id != owner_id or asset.status != "ready"
                            or asset.sha256 != image["sha256"] or asset.byte_size != image["bytes"]):
                        raise InventoryError("legacy_imported_material_unavailable")
                    try:
                        path = checked_path(storage_root.resolve(), asset.object_key)
                        if hashlib.sha256(path.read_bytes()).hexdigest() != image["sha256"]:
                            raise InventoryError("legacy_imported_material_changed")
                    except (OSError, SnapshotError):
                        raise InventoryError("legacy_imported_material_unavailable") from None
                replayed += 1
                reference_count += int(kind != "draft")
                draft_count += int(kind == "draft")
                continue
            if kind != "draft" and (reference_root is None or image["state"] != "available"):
                skipped.append({"kind": kind, "id": identity, "issues": item["issues"]})
                continue
            project = await db.get(Project, source.project_id) if kind != "reference" else None
            if ((kind == "reference" and source.user_id != owner_id)
                    or (kind != "reference" and (project is None or project.user_id != owner_id))):
                raise InventoryError("legacy_material_owner_conflict")
            summary = {"record": source_record(source),
                       "project": {"id": project.id, "name": project.name} if project else None,
                       "issues": item["issues"], "replay": False}
            encoded(summary)  # Bounded private JSON; opaque draft content is never executed.
            material_id = str(uuid5(NAMESPACE_URL, f"kigcraft-v2-legacy-material:{owner_id}:{kind}:{identity}"))
            asset_id = None
            if kind != "draft":
                asset_id = str(uuid5(NAMESPACE_URL, "kigcraft-v2-legacy-reference:" + material_id))
                if await db.get(Asset, asset_id):
                    raise InventoryError("legacy_material_asset_identity_conflict")
                key = install_original(reference_root, reference_key(source.object_key),
                                       storage_root, asset_id, image)
                db.add(Asset(id=asset_id, owner_id=owner_id, object_key=key, sha256=image["sha256"],
                             mime_type=MIME_TYPES[image["format"]], kind="legacy_reference",
                             width=image["width"], height=image["height"], byte_size=image["bytes"], status="ready"))
                await db.flush()
                reference_count += 1
            else:
                draft_count += 1
            db.add(LegacyMaterial(id=material_id, owner_id=owner_id, source_kind=kind, source_id=identity,
                                 source_digest=item["source_digest"], source_summary=summary, asset_id=asset_id))
            await db.flush()
            imported += 1
    return {"imported": imported, "replayed": replayed, "skipped": skipped,
            "references_available": reference_count, "drafts_preserved": draft_count,
            "editor_drafts_converted": 0}
