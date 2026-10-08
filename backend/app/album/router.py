import json
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sessions import get_current_user
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.core.time import utc_now
from app.db.models import AlbumItem as AlbumItemRow
from app.db.models import User
from app.db.session import get_session
from app.images.edit_metadata import embed_edit_recipe_in_png
from app.images.watermark import apply_kigcraft_watermark

router = APIRouter(prefix="/album", tags=["album"])

ALLOWED_IMAGE_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class AlbumItemIn(BaseModel):
    image_url: str = Field(min_length=1)
    recipe: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class AlbumItemOut(BaseModel):
    id: str
    image_url: str
    created_at: str
    recipe: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


def clear_album_items() -> None:
    return None


def _item_to_out(item: AlbumItemRow) -> AlbumItemOut:
    return AlbumItemOut(
        id=item.id,
        image_url=item.image_url,
        created_at=item.created_at.isoformat(),
        recipe=item.recipe,
        metadata=item.metadata_json,
    )


@router.post("/items", response_model=AlbumItemOut)
async def save_album_item(
    payload: AlbumItemIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AlbumItemOut:
    await _ensure_generated_album_url_owner(
        payload.image_url,
        current_user=current_user,
        session=session,
    )
    item = AlbumItemRow(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        image_url=payload.image_url,
        created_at=utc_now(),
        recipe=payload.recipe,
        metadata_json=payload.metadata,
    )
    session.add(item)
    await session.commit()
    return _item_to_out(item)


@router.post("/items/file", response_model=AlbumItemOut)
async def save_album_item_file(
    file: UploadFile = File(...),
    recipe: str | None = Form(default=None),
    metadata: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AlbumItemOut:
    content_type = file.content_type or ""
    suffix = ALLOWED_IMAGE_MEDIA_TYPES.get(content_type)
    if suffix is None:
        raise HTTPException(status_code=400, detail="Unsupported album image type")

    item_id = str(uuid.uuid4())
    parsed_recipe = _parse_optional_json_object(recipe, "recipe")
    parsed_metadata = _parse_optional_json_object(metadata, "metadata")
    safe_name = _safe_image_filename(file.filename, suffix)
    output_dir = resolve_repo_path(get_settings().codex_output_dir) / "album" / item_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / safe_name
    output_path.write_bytes(await file.read())
    apply_kigcraft_watermark(output_path)
    if suffix == ".png" and parsed_recipe is not None:
        embed_edit_recipe_in_png(output_path, parsed_recipe)

    image_url = f"{_public_generated_prefix()}/album/{item_id}/{safe_name}"
    item = AlbumItemRow(
        id=item_id,
        user_id=current_user.id,
        image_url=image_url,
        created_at=utc_now(),
        recipe=parsed_recipe,
        metadata_json=parsed_metadata,
    )
    session.add(item)
    await session.commit()
    return _item_to_out(item)


@router.get("/items", response_model=list[AlbumItemOut])
async def list_album_items(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AlbumItemOut]:
    result = await session.execute(
        select(AlbumItemRow)
        .where(AlbumItemRow.user_id == current_user.id)
        .order_by(AlbumItemRow.created_at.desc(), AlbumItemRow.id.desc())
    )
    return [_item_to_out(item) for item in result.scalars()]


def _safe_image_filename(filename: str | None, suffix: str) -> str:
    stem = Path(filename or "edited").stem
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-") or "edited"
    return f"{safe_stem}{suffix}"


def _public_generated_prefix() -> str:
    return f"/{get_settings().generated_public_prefix.strip('/')}"


async def _ensure_generated_album_url_owner(
    image_url: str,
    *,
    current_user: User,
    session: AsyncSession,
) -> None:
    item_id = _generated_album_item_id_from_url(image_url)
    if item_id is None:
        return
    result = await session.execute(
        select(AlbumItemRow.id)
        .where(AlbumItemRow.id == item_id)
        .where(AlbumItemRow.user_id == current_user.id)
        .limit(1)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Album item not found")


def _generated_album_item_id_from_url(image_url: str) -> str | None:
    path = urlsplit(image_url).path.replace("\\", "/")
    for prefix in _generated_public_prefixes():
        prefix_with_slash = f"{prefix.rstrip('/')}/"
        if not path.startswith(prefix_with_slash):
            continue
        relative_path = path[len(prefix_with_slash) :]
        parts = PurePosixPath(relative_path).parts
        if len(parts) >= 3 and parts[0] == "album" and parts[1]:
            return parts[1]
    return None


def _generated_public_prefixes() -> list[str]:
    normalized = _public_generated_prefix()
    prefixes = [normalized]
    if normalized.startswith("/api/"):
        prefixes.append(normalized.removeprefix("/api"))
    return prefixes


def _parse_optional_json_object(value: str | None, field_name: str) -> dict[str, Any] | None:
    if value is None or value.strip() == "":
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a JSON object")
    return parsed
