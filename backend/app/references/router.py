import re
import shutil
import uuid
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.core.paths import resolve_repo_path


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
) -> dict[str, str]:
    _validate_reference_kind(kind)
    file_name = _safe_filename(file.filename)
    _validate_image_upload(file, file_name)

    upload_id = str(uuid.uuid4())
    reference_root = resolve_repo_path(get_settings().reference_upload_dir)
    upload_dir = reference_root / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / file_name

    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)

    return {
        "object_key": f"references/{upload_id}/{file_name}",
        "file_name": file_name,
    }


@router.get("/{asset_path:path}")
async def get_reference_asset(asset_path: str) -> FileResponse:
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

    reference_root = resolve_repo_path(get_settings().reference_upload_dir).resolve()
    candidate = (reference_root / Path(*posix_path.parts[1:])).resolve()
    try:
        candidate.relative_to(reference_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid reference asset path")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Reference asset not found")
    return FileResponse(candidate, media_type=IMAGE_MEDIA_TYPES[extension])


def clear_uploaded_references() -> None:
    shutil.rmtree(resolve_repo_path(get_settings().reference_upload_dir), ignore_errors=True)


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
