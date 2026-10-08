"""Legacy inventory, with an explicit reviewed-digest import mode.

python -m app.maintenance.v2_migration --owner-id UUID --asset-root DIR --report NEW_JSON
V2_DATABASE_URL must be explicit. Reports contain IDs/provenance, never prompts or credentials.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import warnings
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db.models import AlbumItem, GenerationJob, GenerationOutput, Project, ProjectDraft, ProjectReference, ReferenceAsset, User
from app.design.service import digest
from app.maintenance.legacy_status_adapter import legacy_status

MAX_RECORDS = 10000
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


class InventoryError(ValueError):
    pass


def generated_key(url: str, prefix: str):
    """Only application-relative legacy URLs. No downloads, guessed hosts or URL rewriting."""
    if not isinstance(url, str) or not url or "\\" in url:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    path = unquote(parsed.path)
    # Reject double decoding, controls, drive aliases and noncanonical paths.
    if "%" in path or any(ord(char) < 32 for char in path):
        return None
    normalized = "/" + prefix.strip("/")
    prefixes = [normalized]
    if normalized.startswith("/api/"):
        prefixes.append(normalized[4:])
    for value in prefixes:
        if path.startswith(value + "/"):
            key = path[len(value) + 1:]
            return key if valid_key(key) else None
    return None


def valid_key(key):
    if not isinstance(key, str) or not key or "\\" in key or key.startswith("/"):
        return False
    parts = PurePosixPath(key).parts
    return (str(PurePosixPath(key)) == key and all(
        part not in {"", ".", ".."} and ":" not in part and not part.endswith((".", " "))
        and not any(ord(char) < 32 for char in part) for part in parts))


def image_inventory(root: Path, key: str | None):
    if key is None or not valid_key(key):
        return {"state": "unmapped"}
    root = root.resolve()
    path = root
    for part in PurePosixPath(key).parts:
        path /= part
        if path.is_symlink() or path.is_junction():
            return {"state": "unsafe_path"}
    if not path.resolve().is_relative_to(root):
        return {"state": "unsafe_path"}
    try:
        before = path.stat()
        if not path.is_file():
            return {"state": "missing"}
        if not 0 < before.st_size <= MAX_IMAGE_BYTES:
            return {"state": "invalid_image"}
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return {"state": "invalid_image"}
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return {"state": "changed_during_scan"}
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS or getattr(image, "n_frames", 1) != 1:
                    return {"state": "invalid_image"}
                image.load()
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    return {"state": "invalid_image"}
                return {"state": "available", "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data), "width": image.width, "height": image.height, "format": image.format}
    except FileNotFoundError:
        return {"state": "missing"}
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return {"state": "invalid_image"}


def recipe_inventory(recipe):
    if recipe is None:
        return {"state": "absent", "replay": False}
    # Legacy rendered images are flattened. A recipe must never be applied a second time.
    try:
        fingerprint = digest(recipe)
    except (ValueError, TypeError):
        return {"state": "invalid", "replay": False}
    if not isinstance(recipe, dict):
        return {"state": "invalid", "sha256": fingerprint, "replay": False}
    from app.design.editor_recipe import NormalizedRecipe
    try:
        NormalizedRecipe.model_validate(recipe)
    except (ValueError, TypeError):
        return {"state": "legacy_unverified", "sha256": fingerprint, "replay": False}
    return {"state": "current_shape", "sha256": fingerprint, "replay": False}


async def bounded(db, query):
    rows = list((await db.scalars(query.limit(MAX_RECORDS + 1))).all())
    if len(rows) > MAX_RECORDS:
        raise InventoryError("inventory_batch_too_large")
    return rows


async def inventory(db, owner_id: str, *, asset_root: Path, public_prefix="/api/generated", reference_root: Path | None = None):
    if not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 36 or any(ord(c) < 32 for c in owner_id):
        raise InventoryError("invalid_owner")
    owner = await db.get(User, owner_id)
    if owner is None:
        raise InventoryError("owner_not_found")
    # The caller must keep a repeatable-read/read-only transaction for the entire scan.
    projects = await bounded(db, select(Project).where(Project.user_id == owner_id).order_by(Project.id))
    jobs = await bounded(db, select(GenerationJob).where(GenerationJob.user_id == owner_id).order_by(GenerationJob.id))
    albums = await bounded(db, select(AlbumItem).where(AlbumItem.user_id == owner_id).order_by(AlbumItem.id))
    outputs = await bounded(db, select(GenerationOutput).join(GenerationJob, GenerationOutput.job_id == GenerationJob.id)
                            .where(GenerationJob.user_id == owner_id).order_by(GenerationOutput.id))
    refs = await bounded(db, select(ReferenceAsset).where(ReferenceAsset.user_id == owner_id).order_by(ReferenceAsset.id))
    project_ids = {row.id for row in projects}
    drafts = await bounded(db, select(ProjectDraft).where(ProjectDraft.project_id.in_(project_ids)).order_by(ProjectDraft.project_id))
    project_refs = await bounded(db, select(ProjectReference).where(ProjectReference.project_id.in_(project_ids)).order_by(ProjectReference.id))
    by_job = defaultdict(list)
    for output in outputs:
        by_job[output.job_id].append(output)
    job_map = {row.id: row for row in jobs}
    output_map = {row.id: row for row in outputs}
    output_by_key = defaultdict(list)
    for row in outputs:
        if valid_key(row.object_key) and generated_key(row.image_url, public_prefix) == row.object_key:
            output_by_key[row.object_key].append(row)
    # Check exact object claims across owners without exposing foreign rows in the report.
    output_keys = {row.object_key for row in outputs}
    foreign_keys = set((await db.scalars(select(GenerationOutput.object_key).join(GenerationJob)
        .where(GenerationOutput.object_key.in_(output_keys), GenerationJob.user_id != owner_id))).all())
    album_keys = {row.id: generated_key(row.image_url, public_prefix) for row in albums}
    foreign_album_keys = set()
    # Only ownership of exact legacy URL aliases is considered, never similarity.
    aliases = set()
    for key in filter(None, album_keys.values()):
        normalized = "/" + public_prefix.strip("/")
        aliases.add(normalized + "/" + key)
        if normalized.startswith("/api/"):
            aliases.add(normalized[4:] + "/" + key)
    if aliases:
        foreign_album_keys.update(generated_key(url, public_prefix) for url in (await db.scalars(
            select(AlbumItem.image_url).where(AlbumItem.user_id != owner_id, AlbumItem.image_url.in_(aliases)))).all())
        foreign_album_keys.update((await db.scalars(select(GenerationOutput.object_key).join(GenerationJob)
            .where(GenerationOutput.object_key.in_(set(filter(None, album_keys.values()))),
                   GenerationJob.user_id != owner_id))).all())
    records, groups = [], {}
    job_groups = {}
    for job in jobs:
        issues = []
        payload = job.prompt_payload if isinstance(job.prompt_payload, dict) else {}
        session = job.character_session_id
        payload_session = payload.get("character_session_id")
        if job.project_id not in project_ids:
            issues.append("project_owner_conflict")
        if session and payload_session and session != payload_session:
            issues.append("session_identity_conflict")
        grouping = ["session", owner_id, job.project_id, session] if session and not issues else ["job", owner_id, job.id]
        group = digest(grouping)
        job_groups[job.id] = group
        groups.setdefault(group, {"id": group, "basis": grouping[0],
            "project_id": job.project_id if job.project_id in project_ids else None,
            "character_session_id": session if grouping[0] == "session" else None})
        state = legacy_status(job.status, job.runtime_status, job.accepted_output_index, [r.index for r in by_job[job.id]])
        source = {"provider": job.provider, **{field: payload[field] for field in (
            "provider_preset", "analysis_provider", "analysis_model", "image_provider", "image_model")
            if isinstance(payload.get(field), str)}}
        records.append({"kind": "job", "id": job.id, "group_id": group, "status": state, "provider": source,
            "generation_mode": job.generation_mode, "created_at": job.created_at.isoformat(),
            "issues": sorted(set(issues + state["issues"])), "source_digest": digest({
                "id":job.id,"project":job.project_id,"session":session,"payload":payload,
                "status":state,"provider":job.provider,"mode":job.generation_mode})})
    for output in sorted(outputs, key=lambda item: (job_map[item.job_id].created_at, item.job_id,
            item.index != job_map[item.job_id].accepted_output_index, item.index, item.id)):
        job = job_map[output.job_id]
        key = output.object_key if valid_key(output.object_key) else None
        url_key = generated_key(output.image_url, public_prefix)
        issues = []
        if job.project_id not in project_ids:
            issues.append("project_owner_conflict")
        if output.object_key in foreign_keys:
            issues.append("asset_owner_conflict")
        if url_key != key:
            issues.append("asset_locator_conflict")
        image = {"state": "unmapped"} if issues else image_inventory(asset_root, key)
        if image["state"] != "available":
            issues.append("image_" + image["state"])
        if image["state"] == "available" and (output.width, output.height) != (image["width"], image["height"]):
            issues.append("stored_dimensions_mismatch")
        # Turnaround is an existing composite, never four independently generated views.
        view = "front" if job.generation_mode in {"front_design", "front_revision"} else (
            "composite" if job.generation_mode == "turnaround" else "unknown")
        records.append({"kind": "output", "id": output.id, "job_id": job.id, "group_id": job_groups[job.id],
            "view": view, "image": image, "provider": job.provider, "issues": sorted(set(issues)),
            "source_digest": digest({"id":output.id,"job":job.id,"index":output.index,"object_key":output.object_key,
                "image_url":output.image_url,"width":output.width,"height":output.height,"landmarks":output.landmarks})})
    for album in albums:
        issues = []
        linked = output_map.get(album.output_id)
        key = album_keys[album.id]
        link_basis = "explicit_output_id" if linked else None
        exact = output_by_key.get(key, [])
        if not album.output_id and len(exact) == 1:
            linked = exact[0]
            link_basis = "exact_owned_object"
        elif not album.output_id and len(exact) > 1:
            issues.append("ambiguous_output_identity")
        job = job_map[linked.job_id] if linked else None
        if album.output_id and linked is None:
            issues.append("output_owner_or_identity_conflict")
        if (album.project_id and album.project_id not in project_ids) or (job and job.project_id not in project_ids):
            issues.append("project_owner_conflict")
        if linked and album.project_id and album.project_id != job.project_id:
            issues.append("project_identity_conflict")
        # File uploads prove ownership through album/<row-id>/..., or a verified linked output.
        upload = bool(key and PurePosixPath(key).parts[:2] == ("album", album.id))
        matches_output = bool(linked and key == linked.object_key)
        if key in foreign_album_keys:
            issues.append("asset_owner_conflict")
        if not upload and not matches_output:
            issues.append("asset_ownership_unproven")
        group = job_groups[job.id] if job and not issues else digest(["album", owner_id, album.id])
        groups.setdefault(group, {"id": group, "basis": "album", "project_id": album.project_id if album.project_id in project_ids else None,
                                  "character_session_id": None})
        image = {"state": "unmapped"} if issues else image_inventory(asset_root, key)
        if image["state"] != "available":
            issues.append("image_" + image["state"])
        recipe = recipe_inventory(album.recipe)
        if recipe["state"] in {"invalid", "legacy_unverified"}:
            issues.append("recipe_" + recipe["state"])
        records.append({"kind": "album", "id": album.id, "group_id": group,
            "linked_output_id": linked.id if linked else None, "link_basis": link_basis, "image": image, "recipe": recipe,
            "view": "unknown", "provider": job.provider if job else None, "issues": sorted(set(issues)),
            "source_digest": digest({"id":album.id,"project":album.project_id,"output":album.output_id,
                "url":album.image_url,"recipe":album.recipe,"metadata":album.metadata_json})})
    # Check stored reference bytes explicitly; opaque old draft JSON is never replayed.
    from app.maintenance.legacy_materials import inventory_materials
    ancillary = await inventory_materials(db, owner_id, refs, project_refs, drafts, reference_root=reference_root)
    all_records = [*records, *(row for collection in ancillary.values() for row in collection)]
    report = {"format":2,"mode":"read_only_inventory","owner_id":owner_id,
        "account":{"has_password":bool(owner.password_hash),"email_verified":owner.email_verified_at is not None},
        "projects":[{"id":p.id,"source_digest":digest({"name":p.name,"created_at":p.created_at.isoformat()})} for p in projects],
        "groups":sorted(groups.values(),key=lambda g:g["id"]), "records":records, "ancillary":ancillary,
        "counts":{"projects":len(projects),"jobs":len(jobs),"outputs":len(outputs),"albums":len(albums),
            "references":len(refs),"project_references":len(project_refs),"drafts":len(drafts)},
        "issues":dict(sorted(Counter(issue for row in all_records for issue in row["issues"]).items())),
        "not_performed":["database_writes","asset_copies","wallet_changes","chat_reconstruction","model_calls"]}
    report["inventory_digest"] = digest(report)
    return report


async def run(args):
    url = os.environ.get("V2_DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        raise InventoryError("explicit_database_url_required")
    if args.report.exists():
        raise InventoryError("report_already_exists")
    archive_target = getattr(args, "archive_materials", None)
    if archive_target is not None and args.report.resolve().is_relative_to(archive_target.resolve()):
        raise InventoryError("archive_receipt_must_be_external")
    engine = create_async_engine(url, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="READ COMMITTED" if args.apply else "REPEATABLE READ")
            async with connection.begin():
                if not args.apply:
                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                from sqlalchemy.ext.asyncio import AsyncSession
                async with AsyncSession(connection) as db:
                    if args.apply:
                        from app.maintenance.legacy_import import apply_inventory
                        if args.storage_root is None or not args.expected_digest:
                            raise InventoryError("reviewed_inventory_and_storage_required")
                        report = await apply_inventory(db,args.owner_id,asset_root=args.asset_root,storage_root=args.storage_root,
                            expected_digest=args.expected_digest,public_prefix=args.public_prefix,reference_root=getattr(args,"reference_root",None),
                            include_materials=getattr(args,"include_materials",False))
                    elif archive_target is not None:
                        from app.maintenance.legacy_materials_archive import archive_materials
                        if not args.expected_digest:
                            raise InventoryError("reviewed_inventory_required")
                        report = await archive_materials(db, args.owner_id, asset_root=args.asset_root,
                            reference_root=getattr(args,"reference_root",None), destination=archive_target,
                            expected_digest=args.expected_digest, public_prefix=args.public_prefix)
                    else:
                        report = await inventory(db, args.owner_id, asset_root=args.asset_root, public_prefix=args.public_prefix, reference_root=getattr(args,"reference_root",None))
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
        except OSError:
            raise InventoryError("report_write_failed_import_may_be_committed" if args.apply else (
                "report_write_failed_archive_may_be_complete" if archive_target is not None else "report_write_failed")) from None
        print(json.dumps({"status":report.get("status","inventoried"),"counts":report.get("counts"),
                          "issues":report.get("issues"),"imported":report.get("imported"),"replayed":report.get("replayed"),
                          "inventory_digest":report["inventory_digest"],
                          **({"materials": {key: report["materials"][key] for key in ("imported","replayed","references_available","drafts_preserved","editor_drafts_converted")}} if report.get("materials") else {}),
                          **({key:report[key] for key in ("manifest_sha256","copied_images","unavailable_images")} if archive_target is not None else {})}))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--asset-root", required=True, type=Path)
    parser.add_argument("--public-prefix", default="/api/generated")
    parser.add_argument("--reference-root", type=Path, help="Explicit legacy reference_upload_dir; never inferred from generated assets")
    parser.add_argument("--report", required=True, type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--archive-materials", type=Path, help="New private reference/draft preservation directory; no database writes")
    parser.add_argument("--expected-digest")
    parser.add_argument("--storage-root", type=Path)
    parser.add_argument("--include-materials", action="store_true", help="With --apply: copy verified references and preserve opaque project drafts")
    args = parser.parse_args()
    if args.include_materials and not args.apply:
        parser.error("--include-materials requires --apply")
    try:
        asyncio.run(run(args))
    except Exception as exc:
        # No connection URL, credentials, filenames, or source payload in diagnostics.
        parser.exit(1, json.dumps({"status":"refused","code":str(exc) if isinstance(exc, InventoryError) else "inventory_unavailable"})+"\n")


if __name__ == "__main__":
    main()
