import re
import shutil
import uuid
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import UserRole
from app.auth.sessions import get_current_user
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.db.models import ReferenceAsset, User
from app.db.session import get_session


router = APIRouter(prefix="/references", tags=["references"])

ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


@router.post("")
async def upload_reference(
    kind: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    _validate_reference_kind(kind)
    file_name = _safe_filename(file.filename)
    _validate_image_upload(file, file_name)

    upload_id = str(uuid.uuid4())
    object_key = f"references/{upload_id}/{file_name}"
    reference_root = resolve_repo_path(get_settings().reference_upload_dir)
    upload_dir = reference_root / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / file_name

    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)

    session.add(
        ReferenceAsset(
            user_id=current_user.id,
            kind=kind,
            object_key=object_key,
            file_name=file_name,
            mime_type=file.content_type or IMAGE_MEDIA_TYPES[Path(file_name).suffix.lower()],
        )
    )
    await session.commit()

    return {
        "object_key": object_key,
        "file_name": file_name,
    }


@router.get("/{asset_path:path}")
async def get_reference_asset(
    asset_path: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    normalized_path = _normalized_reference_path(asset_path)
    await _ensure_reference_asset_access(
        object_key=normalized_path,
        current_user=current_user,
        session=session,
    )
    file_path = _resolve_reference_asset(normalized_path)
    return FileResponse(file_path, media_type=IMAGE_MEDIA_TYPES[file_path.suffix.lower()])


def clear_uploaded_references() -> None:
    shutil.rmtree(resolve_repo_path(get_settings().reference_upload_dir), ignore_errors=True)


def _normalized_reference_path(asset_path: str) -> str:
    normalized = asset_path.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    if (
        posix_path.is_absolute()
        or any(part == ".." for part in posix_path.parts)
        or len(posix_path.parts) < 2
        or posix_path.parts[0] != "references"
    ):
        raise HTTPException(status_code=400, detail="Invalid reference asset path")

    extension = Path(posix_path.name).suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported reference image type")
    return "/".join(posix_path.parts)


def _resolve_reference_asset(asset_path: str) -> Path:
    posix_path = PurePosixPath(asset_path)
    reference_root = resolve_repo_path(get_settings().reference_upload_dir).resolve()
    candidate = (reference_root / Path(*posix_path.parts[1:])).resolve()
    try:
        candidate.relative_to(reference_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid reference asset path")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Reference asset not found")
    return candidate


async def _ensure_reference_asset_access(
    *,
    object_key: str,
    current_user: User,
    session: AsyncSession,
) -> None:
    owner_id = await _reference_asset_owner_id(object_key=object_key, session=session)
    if current_user.role == UserRole.admin:
        return
    if owner_id is not None:
        if owner_id == current_user.id:
            return
        raise HTTPException(status_code=404, detail="Reference asset not found")

    raise HTTPException(status_code=404, detail="Reference asset not found")


async def _ensure_uploaded_reference_keys_access(
    reference_keys: list[object],
    *,
    current_user: User,
    session: AsyncSession,
) -> set[str]:
    stale_object_keys: set[str] = set()
    for reference_key in reference_keys:
        object_key = await _ensure_uploaded_reference_key_access(
            reference_key=reference_key,
            current_user=current_user,
            session=session,
        )
        if object_key is not None:
            stale_object_keys.add(object_key)
    if stale_object_keys:
        await session.execute(
            delete(ReferenceAsset)
            .where(ReferenceAsset.object_key.in_(stale_object_keys))
            .where(ReferenceAsset.user_id == current_user.id)
        )
        await session.flush()
    return stale_object_keys


async def _ensure_uploaded_reference_key_access(
    *,
    reference_key: object,
    current_user: User,
    session: AsyncSession,
) -> str | None:
    object_key = _reference_key_object_key(reference_key)
    if object_key is None:
        return None

    owner_id = await _reference_asset_owner_id(object_key=object_key, session=session)
    if owner_id is None:
        return object_key

    if current_user.role != UserRole.admin and owner_id != current_user.id:
        return object_key

    try:
        _resolve_reference_asset(object_key)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return object_key
    return None


async def _reference_asset_owner_id(*, object_key: str, session: AsyncSession) -> str | None:
    result = await session.execute(
        select(ReferenceAsset.user_id).where(ReferenceAsset.object_key == object_key).limit(1)
    )
    return result.scalar_one_or_none()


def _reference_key_object_key(reference_key: object) -> str | None:
    if not isinstance(reference_key, str) or not reference_key:
        return None
    normalized = reference_key.replace("\\", "/")
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1]
    try:
        return _normalized_reference_path(normalized)
    except HTTPException:
        return None


def _validate_reference_kind(kind: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", kind):
        raise HTTPException(status_code=400, detail="Invalid reference kind")


def _validate_image_upload(file: UploadFile, file_name: str) -> None:
    extension = Path(file_name).suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported reference image type")
    if file.content_type and file.content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported reference image type")


def _safe_filename(filename: str | None) -> str:
    name = PurePosixPath((filename or "reference").replace("\\", "/")).name
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip(".-")
    safe_name = f"{safe_stem or 'reference'}{suffix}"
    if safe_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid reference filename")
    return safe_name
