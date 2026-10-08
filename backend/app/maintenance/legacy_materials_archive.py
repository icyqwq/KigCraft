"""A portable, private preservation archive; never an executable V2 editor document."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import select
from app.db.models import Project, ProjectDraft, ProjectReference, ReferenceAsset
from app.design.service import digest
from app.maintenance.codex_snapshot import checked_path, SnapshotError
from app.maintenance.legacy_materials import reference_key, source_record
from app.maintenance.v2_migration import bounded, image_inventory, inventory, InventoryError, MAX_IMAGE_BYTES

MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_IMAGE_BYTES = 512 * 1024 * 1024
SCOPE = "legacy_reference_and_project_draft_preservation"


def encoded(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > MAX_METADATA_BYTES:
        raise InventoryError("legacy_archive_metadata_too_large")
    return data


def write_new(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def read_json(root, relative):
    try:
        path = checked_path(root, relative)
        with path.open("rb") as stream:
            data = stream.read(MAX_METADATA_BYTES + 1)
        if len(data) > MAX_METADATA_BYTES:
            raise InventoryError("legacy_archive_metadata_too_large")
        return json.loads(data), hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, SnapshotError):
        raise InventoryError("legacy_archive_invalid_metadata") from None


async def archive_materials(db, owner_id, *, asset_root, reference_root, destination, expected_digest,
                            public_prefix="/api/generated"):
    # Caller holds a read-only repeatable-read snapshot. No business data is mutated.
    report = await inventory(db, owner_id, asset_root=asset_root, reference_root=reference_root, public_prefix=public_prefix)
    if report["inventory_digest"] != expected_digest:
        raise InventoryError("legacy_inventory_changed")
    if reference_root is None and (report["counts"]["references"] or report["counts"]["project_references"]):
        raise InventoryError("explicit_reference_root_required")
    projects = await bounded(db, select(Project).where(Project.user_id == owner_id).order_by(Project.id))
    project_ids = [row.id for row in projects]
    source_rows = {
        "references": await bounded(db, select(ReferenceAsset).where(ReferenceAsset.user_id == owner_id).order_by(ReferenceAsset.id)),
        "project_references": await bounded(db, select(ProjectReference).where(ProjectReference.project_id.in_(project_ids)).order_by(ProjectReference.id)),
        "drafts": await bounded(db, select(ProjectDraft).where(ProjectDraft.project_id.in_(project_ids)).order_by(ProjectDraft.project_id)),
    }
    document = {
        "format": 1, "scope": SCOPE, "owner_id": owner_id,
        "projects": [{"id": row.id, "name": row.name, "created_at": row.created_at.isoformat()} for row in projects],
        "materials": {name: [source_record(row) for row in rows] for name, rows in source_rows.items()},
    }
    metadata = encoded(document)
    report_data = encoded(report)
    images = []
    total = 0
    for name in ("references", "project_references"):
        for row, inspected in zip(source_rows[name], report["ancillary"][name], strict=True):
            if row.id != inspected["id"] or digest(source_record(row)) != inspected["source_digest"]:
                raise InventoryError("legacy_inventory_changed")
            image = inspected["image"]
            if image["state"] == "available":
                total += image["bytes"]
                if total > MAX_ARCHIVE_IMAGE_BYTES:
                    raise InventoryError("legacy_archive_images_too_large")
                relative = "images/" + digest([name, row.id]) + {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[image["format"]]
                images.append({"collection": name, "source_id": row.id, "path": relative, "image": image})

    # Refuse existing destinations, even an incomplete archive. Preserve evidence for inspection.
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise InventoryError("legacy_archive_target_exists") from None
    write_new(destination / "INCOMPLETE", b"Archive has not passed verification.\n")
    (destination / "images").mkdir()
    write_new(destination / "materials.json", metadata)
    write_new(destination / "inventory.json", report_data)
    by_identity = {(name, row.id): row for name, rows in source_rows.items() if name != "drafts" for row in rows}
    for item in images:
        row = by_identity[(item["collection"], item["source_id"])]
        try:
            source = checked_path(reference_root.resolve(), reference_key(row.object_key))
            with source.open("rb") as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
        except (OSError, SnapshotError):
            raise InventoryError("legacy_reference_changed") from None
        if len(data) != item["image"]["bytes"] or hashlib.sha256(data).hexdigest() != item["image"]["sha256"]:
            raise InventoryError("legacy_reference_changed")
        write_new(destination / item["path"], data)
    manifest = {"format": 1, "scope": SCOPE, "owner_id": owner_id, "inventory_digest": expected_digest,
                "metadata_sha256": hashlib.sha256(metadata).hexdigest(), "inventory_sha256": hashlib.sha256(report_data).hexdigest(),
                "images": images}
    write_new(destination / "manifest.json", encoded(manifest))
    result = verify_archive(destination, expected_owner=owner_id, allow_incomplete=True)
    (destination / "INCOMPLETE").unlink()
    return {**result, "status": "archived", "wallet_changed": False, "model_calls": 0, "database_writes": False}


def verify_archive(root: Path, *, expected_owner: str, expected_manifest_sha256=None, allow_incomplete=False):
    """Check corruption/ownership/completeness; matching owner is not permission to import."""
    try:
        return _verify_archive(root, expected_owner, expected_manifest_sha256, allow_incomplete)
    except InventoryError:
        raise
    except (OSError, SnapshotError, ValueError, TypeError, KeyError, AttributeError):
        raise InventoryError("legacy_archive_invalid") from None


def _verify_archive(root, owner, expected_sha, allow_incomplete):
    root = root.resolve()
    if not allow_incomplete and (root / "INCOMPLETE").exists():
        raise InventoryError("legacy_archive_incomplete")
    manifest, manifest_sha = read_json(root, "manifest.json")
    if expected_sha is not None and manifest_sha != expected_sha:
        raise InventoryError("legacy_archive_manifest_changed")
    report, report_sha = read_json(root, "inventory.json")
    document, document_sha = read_json(root, "materials.json")
    if (manifest["format"] != 1 or document["format"] != 1 or report["format"] != 2 or
            manifest["scope"] != SCOPE or document["scope"] != SCOPE or
            any(item["owner_id"] != owner for item in (manifest, report, document)) or
            manifest["metadata_sha256"] != document_sha or manifest["inventory_sha256"] != report_sha):
        raise InventoryError("legacy_archive_identity_or_hash_mismatch")
    if manifest["inventory_digest"] != report["inventory_digest"] or digest(
            {key: value for key, value in report.items() if key != "inventory_digest"}) != report["inventory_digest"]:
        raise InventoryError("legacy_archive_inventory_mismatch")
    projects = {row["id"]: row for row in document["projects"]}
    if len(projects) != len(document["projects"]) or set(projects) != {row["id"] for row in report["projects"]}:
        raise InventoryError("legacy_archive_project_mismatch")
    for row in report["projects"]:
        project = projects[row["id"]]
        if digest({"name": project["name"], "created_at": project["created_at"]}) != row["source_digest"]:
            raise InventoryError("legacy_archive_project_mismatch")
    expected_images = {}
    if set(document["materials"]) != {"references", "project_references", "drafts"}:
        raise InventoryError("legacy_archive_materials_mismatch")
    for name, records in document["materials"].items():
        key = "project_id" if name == "drafts" else "id"
        by_id = {row[key]: row for row in records}
        inspected = report["ancillary"][name]
        if len(by_id) != len(records) or set(by_id) != {row[key] for row in inspected}:
            raise InventoryError("legacy_archive_materials_mismatch")
        for item in inspected:
            row = by_id[item[key]]
            if digest(row) != item["source_digest"] or (row.get("user_id", owner) != owner):
                raise InventoryError("legacy_archive_source_mismatch")
            if name != "references" and row["project_id"] not in projects:
                raise InventoryError("legacy_archive_source_mismatch")
            if name != "drafts" and item["image"]["state"] == "available":
                expected_images[(name, item[key])] = item["image"]
    seen = set()
    paths = {"manifest.json", "inventory.json", "materials.json"}
    if allow_incomplete:
        paths.add("INCOMPLETE")
    total = 0
    for entry in manifest["images"]:
        identity = (entry["collection"], entry["source_id"])
        if identity in seen or identity not in expected_images or entry["image"] != expected_images[identity]:
            raise InventoryError("legacy_archive_image_mismatch")
        if entry["path"] in paths or not entry["path"].startswith("images/"):
            raise InventoryError("legacy_archive_image_mismatch")
        total += entry["image"]["bytes"]
        if total > MAX_ARCHIVE_IMAGE_BYTES:
            raise InventoryError("legacy_archive_images_too_large")
        checked_path(root, entry["path"])
        if image_inventory(root, entry["path"]) != entry["image"]:
            raise InventoryError("legacy_archive_image_mismatch")
        seen.add(identity)
        paths.add(entry["path"])
    if seen != set(expected_images):
        raise InventoryError("legacy_archive_missing_images")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise InventoryError("legacy_archive_linked_file")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != paths:
        raise InventoryError("legacy_archive_unexpected_files")
    refs = [*report["ancillary"]["references"], *report["ancillary"]["project_references"]]
    return {"status": "verified", "scope": SCOPE, "inventory_digest": report["inventory_digest"],
            "manifest_sha256": manifest_sha, "counts": {key: len(rows) for key, rows in document["materials"].items()},
            "copied_images": len(seen), "unavailable_images": sum(row["image"]["state"] != "available" for row in refs),
            "drafts_replayed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_archive(args.archive, expected_owner=args.owner_id,
                                       expected_manifest_sha256=args.manifest_sha256)))
    except InventoryError as exc:
        parser.exit(1, json.dumps({"status": "refused", "code": str(exc)}) + "\n")


if __name__ == "__main__":
    main()
