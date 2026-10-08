"""Apply a reviewed legacy inventory without inventing operations, chat, or quota entries."""
from __future__ import annotations
from app.assets.coordination import storage_writer
from app.core.time import utc_now
import hashlib
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select,text
from app.db.models import AlbumItem, GenerationOutput, Project, User
from app.design.models import Asset,Branch,Design,LegacyImport,Revision
from app.design.service import create_design,event
from app.maintenance.codex_snapshot import checked_path,SnapshotError
from app.maintenance.v2_migration import inventory,generated_key,InventoryError


def install_original(source_root, key, storage_root, asset_id, expected):
    source = checked_path(source_root.resolve(), key)
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected["sha256"] or len(data) != expected["bytes"]:
        raise InventoryError("legacy_image_changed")
    extension = {"PNG":".png","JPEG":".jpg","WEBP":".webp"}[expected["format"]]
    relative = f"legacy/{asset_id}{extension}"
    directory = storage_root.resolve() / "legacy"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or directory.is_junction():
        raise InventoryError("unsafe_legacy_storage")
    target = directory / (asset_id + extension)
    # Unique temporary file and no-replace link make a failed DB commit safely retryable.
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile(dir=directory, prefix=".legacy-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or target.is_junction() or hashlib.sha256(target.read_bytes()).hexdigest() != expected["sha256"]:
                raise InventoryError("legacy_asset_identity_conflict") from None
    finally:
        temporary.unlink(missing_ok=True)
    return relative


async def apply_inventory(db, owner_id, *, asset_root: Path, storage_root: Path, expected_digest: str, public_prefix="/api/generated", reference_root: Path | None = None, include_materials: bool = False):
    from app.assets.repository import require_filesystem
    from app.core.config import get_settings
    require_filesystem(get_settings())
    await storage_writer(db)
    if not expected_digest or len(expected_digest) != 64:
        raise InventoryError("reviewed_inventory_required")
    # Administrative migration only; lock old input tables while checking/copying exact files.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity,7272192218))"),{"identity":owner_id})
    await db.execute(text("SET LOCAL lock_timeout='5s'"))
    await db.execute(text("LOCK TABLE projects,project_drafts,project_references,reference_assets,generation_jobs,generation_outputs,album_items IN SHARE MODE"))
    owner=await db.scalar(select(User).where(User.id==owner_id).with_for_update())
    if owner is None:
        raise InventoryError("owner_not_found")
    if not owner.password_hash or owner.email_verified_at is None or owner.is_banned:
        raise InventoryError("legacy_identity_requires_review")
    report=await inventory(db,owner_id,asset_root=asset_root,public_prefix=public_prefix,reference_root=reference_root)
    if report["inventory_digest"]!=expected_digest:
        raise InventoryError("legacy_inventory_changed")
    if any(row["status"]["terminal"] is not True for row in report["records"] if row["kind"]=="job"):
        raise InventoryError("legacy_work_not_drained")
    groups={row["id"]:row for row in report["groups"]}
    mappings={}
    imported,replayed,skipped=0,0,[]
    retired_assets=set()
    designs={}
    # No current pointer is moved on a design modified since its initial import.
    owned_group_keys={row["group_id"] for row in report["records"]}
    previous=list((await db.scalars(select(LegacyImport).where(
        LegacyImport.owner_id==owner_id,LegacyImport.group_key.in_(owned_group_keys)))).all())
    for old in previous:
        designs[old.group_key]=await db.get(Design,old.design_id)
    for row in report["records"]:
        old=await db.scalar(select(LegacyImport).where(LegacyImport.source_kind==row["kind"],LegacyImport.source_id==row["id"]))
        image_hash=(row.get("image") or {}).get("sha256")
        if old:
            if old.owner_id!=owner_id or old.source_digest!=row["source_digest"] or old.image_sha256!=image_hash or old.group_key!=row["group_id"]:
                raise InventoryError("legacy_source_changed_after_import")
            if old.revision_id:
                revision=await db.get(Revision,old.revision_id)
                asset=await db.get(Asset,revision.clean_asset_id) if revision else None
                if asset is None or asset.owner_id!=owner_id or asset.sha256!=image_hash:
                    raise InventoryError("legacy_imported_asset_unavailable")
                if asset.status != "ready":
                    from app.maintenance.trash_assets import retired_asset
                    target=designs.get(old.group_key)
                    if (target is None or target.owner_id!=owner_id or target.deleted_at is None
                            or target.trash_restore_until is None or target.trash_restore_until>utc_now()
                            or not await retired_asset(db,asset)):
                        raise InventoryError("legacy_imported_asset_unavailable")
                    retired_assets.add(asset.id)
                else:
                    try:
                        stored=checked_path(storage_root.resolve(),asset.object_key)
                        if hashlib.sha256(stored.read_bytes()).hexdigest()!=image_hash:
                            raise InventoryError("legacy_imported_asset_changed")
                    except (OSError,SnapshotError):
                        raise InventoryError("legacy_imported_asset_unavailable") from None
            mappings[(row["kind"],row["id"])]=old;replayed+=1
            continue
        if ("project_owner_conflict" in row["issues"] or
                row["kind"]!="job" and row["image"]["state"]!="available"):
            skipped.append({"kind":row["kind"],"id":row["id"],"issues":row["issues"]})
            continue
        group=groups[row["group_id"]]
        design=designs.get(group["id"])
        fresh=design is None
        if fresh:
            project=await db.get(Project,group["project_id"]) if group["project_id"] else None
            title=("Legacy - "+project.name)[:200] if project else "Legacy record"
            collision=await db.scalar(select(Design.id).where(Design.owner_id==owner_id,Design.client_request_id=="legacy-"+group["id"]))
            if collision is not None:
                raise InventoryError("legacy_target_identity_conflict")
            design=await create_design(db,owner_id,"legacy-"+group["id"],title,enforce_limit=False)
            designs[group["id"]]=design
        if design is None or design.owner_id!=owner_id or design.deleted_at is not None:
            raise InventoryError("legacy_target_unavailable")
        branch=await db.get(Branch,design.last_branch_id)
        revision=None
        summary=dict(row)
        if row["kind"]!="job":
            alias=mappings.get(("output",row.get("linked_output_id"))) if row["kind"]=="album" else None
            if alias and alias.image_sha256==image_hash and alias.design_id==design.id:
                revision=await db.get(Revision,alias.revision_id)
            if revision is None:
                source=await db.get(GenerationOutput,row["id"]) if row["kind"]=="output" else await db.get(AlbumItem,row["id"])
                key=source.object_key if row["kind"]=="output" else generated_key(source.image_url,public_prefix)
                asset_id=str(uuid5(NAMESPACE_URL,f"kigcraft-v2-legacy:{owner_id}:{row['kind']}:{row['id']}"))
                object_key=install_original(asset_root,key,storage_root,asset_id,row["image"])
                asset=Asset(id=asset_id,owner_id=owner_id,object_key=object_key,sha256=image_hash,
                    mime_type={"PNG":"image/png","JPEG":"image/jpeg","WEBP":"image/webp"}[row["image"]["format"]],
                    kind="legacy",width=row["image"]["width"],height=row["image"]["height"],byte_size=row["image"]["bytes"],status="ready")
                db.add(asset);await db.flush()
                if branch.current_revision_id is not None:
                    # Old artifacts have no proven model checkpoint or ancestry. Give each
                    # distinct image an independently editable branch instead of inventing lineage.
                    branch=Branch(design_id=design.id)
                    db.add(branch);await db.flush()
                revision=Revision(design_id=design.id,branch_id=branch.id,clean_asset_id=asset.id,root_source_asset_id=asset.id,
                    source_kind="legacy",view=row["view"],recipe=None,context_checkpoint=None,operation_id=None,
                    parent_revision_id=None,client_request_id="legacy-"+row["kind"]+"-"+row["id"],request_digest=row["source_digest"],
                    created_at=source.created_at)
                db.add(revision);await db.flush()
                # Only populate a still-empty branch, avoiding inferred ancestry or overwriting later user work.
                if branch.current_revision_id is None:
                    branch.current_revision_id=revision.id;branch.state_version+=1
                await event(db,design.id,"image.ready",{"revision_id":revision.id,"asset_id":asset.id,"is_current":branch.current_revision_id==revision.id})
            if row["kind"]=="album":
                album=await db.get(AlbumItem,row["id"])
                summary["original_recipe"]=album.recipe
                summary["original_metadata"]=album.metadata_json
        mapping=LegacyImport(owner_id=owner_id,source_kind=row["kind"],source_id=row["id"],
            source_digest=row["source_digest"],source_summary=summary,image_sha256=image_hash,group_key=row["group_id"],
            design_id=design.id,revision_id=revision.id if revision else None)
        db.add(mapping);await db.flush();mappings[(row["kind"],row["id"])]=mapping;imported+=1
    materials = None
    if include_materials:
        from app.maintenance.legacy_material_import import import_materials
        materials = await import_materials(db, owner_id, report, reference_root=reference_root, storage_root=storage_root)
    preserved = {name: len(rows) for name, rows in report["ancillary"].items()}
    if materials:
        preserved["references"] = sum(row["kind"] == "reference" for row in materials["skipped"])
        preserved["project_references"] = sum(row["kind"] == "project_reference" for row in materials["skipped"])
    return {"status":"applied","imported":imported,"replayed":replayed,"skipped":skipped,
            "design_ids":sorted(d.id for d in designs.values()),"inventory_digest":report["inventory_digest"],
            "wallet_changed":False,"model_calls":0,"retired_assets":sorted(retired_assets),
            "preserved_only":preserved,
            "ancillary_imported":bool(materials and (materials["imported"] or materials["replayed"])), "materials":materials}
