"""Inventory old reference bytes and opaque project drafts without interpreting them."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from sqlalchemy import select
from app.db.models import Project, ProjectReference, ReferenceAsset
from app.design.service import digest
from app.maintenance.v2_migration import image_inventory, valid_key

MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def reference_key(key):
    """Legacy reference_upload_dir contains the contents of the references/ namespace."""
    if not valid_key(key):
        return None
    parts = PurePosixPath(key).parts
    if len(parts) < 3 or parts[0] != "references":
        return None
    return "/".join(parts[1:])


def source_record(row):
    # Explicit allowlists: no account/auth data and no inferred editor document.
    if isinstance(row, ReferenceAsset):
        fields = ("id", "user_id", "kind", "object_key", "file_name", "mime_type", "created_at")
    elif isinstance(row, ProjectReference):
        fields = ("id", "project_id", "kind", "object_key", "mime_type", "width", "height", "created_at")
    else:
        fields = ("project_id", "data", "updated_at")
    return {field: (value.isoformat() if hasattr(value, "isoformat") else value)
            for field in fields for value in [getattr(row, field)]}


async def inventory_materials(db, owner_id, refs, project_refs, drafts, *, reference_root: Path | None):
    keys = {row.object_key for row in [*refs, *project_refs]}
    foreign_keys = set((await db.scalars(select(ReferenceAsset.object_key).where(
        ReferenceAsset.object_key.in_(keys), ReferenceAsset.user_id != owner_id))).all())
    foreign_keys.update((await db.scalars(select(ProjectReference.object_key).join(Project).where(
        ProjectReference.object_key.in_(keys), Project.user_id != owner_id))).all())

    def reference(row):
        issues = []
        key = reference_key(row.object_key)
        if row.object_key in foreign_keys:
            issues.append("asset_owner_conflict")
        if key is None:
            issues.append("asset_locator_unmapped")
        if issues:
            image = {"state": "unmapped"}
        elif reference_root is None:
            image = {"state": "root_not_configured"}
        else:
            image = image_inventory(reference_root, key)
        if image["state"] != "available":
            issues.append("image_" + image["state"])
        else:
            if row.mime_type != MIME_TYPES[image["format"]]:
                issues.append("stored_mime_mismatch")
            if isinstance(row, ProjectReference) and (row.width, row.height) != (image["width"], image["height"]):
                issues.append("stored_dimensions_mismatch")
        return {"id": row.id, "kind": row.kind,
                **({"project_id": row.project_id} if isinstance(row, ProjectReference) else {}),
                "source_digest": digest(source_record(row)), "image": image, "issues": sorted(issues)}

    return {
        "references": [reference(row) for row in refs],
        "project_references": [reference(row) for row in project_refs],
        "drafts": [{"project_id": row.project_id, "source_digest": digest(source_record(row)),
                    "data_state": "object" if isinstance(row.data, dict) else "non_object",
                    "format_state": "uninterpreted", "replay": False,
                    "issues": [] if isinstance(row.data, dict) else ["draft_non_object"]}
                   for row in drafts],
    }
