from __future__ import annotations
import asyncio
import hashlib
import io
import warnings
from pathlib import Path
from uuid import uuid4
from PIL import Image, ImageOps, UnidentifiedImageError
from app.design.models import Asset
from app.assets.coordination import storage_writer
from app.assets.repository import asset_store, storage_io
from app.design.service import ApplicationError
from app.images.encode import MIME, SUFFIX, to_jpeg

PNG_MAGIC = bytes([0x89]) + b"PNG"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_PIXELS = 25_000_000


def normalize_upload(data: bytes) -> tuple[bytes, int, int]:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ApplicationError("image_too_large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_UPLOAD_PIXELS:
                    raise ApplicationError("image_too_large")
                if getattr(image, "n_frames", 1) != 1:
                    raise ApplicationError("animated_image_not_supported")
                image.load()
                normalized = ImageOps.exif_transpose(image)
                see_through = False
                if "A" in normalized.getbands() or "transparency" in normalized.info:
                    low, high = normalized.convert("RGBA").getchannel("A").getextrema()
                    if high == 0:
                        raise ApplicationError("empty_image")
                    see_through = low < 255
                # Strip EXIF/GPS. Keep an embedded color profile for faithful display.
                profile = image.info.get("icc_profile")
                if profile and len(profile) > 512 * 1024:
                    raise ApplicationError("invalid_color_profile")
                if see_through:
                    # Transparency carries meaning (an edit mask, an annotation overlay): keep it, losslessly.
                    result = io.BytesIO()
                    normalized.convert("RGBA").save(result, format="PNG", icc_profile=profile)
                    return result.getvalue(), normalized.width, normalized.height
                # Ordinary pictures are kept as one compact JPEG.
                return to_jpeg(normalized, icc_profile=profile), normalized.width, normalized.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ApplicationError("invalid_image") from None


def upload_asset(data: bytes, owner_id: str, storage_root: Path, *, kind="reference") -> Asset:
    """Synchronous fixture helper; production writes use upload_in_transaction."""
    return install_upload(normalize_upload(data), owner_id, storage_root, kind=kind)


def install_upload(normalized, owner_id: str, storage_root: Path, *, kind="reference") -> Asset:
    data, width, height = normalized
    asset_id = str(uuid4())
    suffix, mime = (".png", "image/png") if data.startswith(PNG_MAGIC) else (SUFFIX, MIME)
    key = f"uploads/{asset_id}{suffix}"
    digest = hashlib.sha256(data).hexdigest()
    asset_store(storage_root).put(key, data, digest, mime)
    return Asset(id=asset_id, owner_id=owner_id, object_key=key, sha256=digest,
                 kind=kind, mime_type=mime, width=width, height=height, byte_size=len(data), status="ready")


def asset_path(asset: Asset, storage_root: Path) -> Path:
    return asset_store(storage_root).materialize(asset)


async def upload_in_transaction(db, data: bytes, owner_id: str, storage_root: Path) -> Asset:
    # Cancellation during CPU work cannot leave a background filesystem writer.
    normalized = await asyncio.to_thread(normalize_upload, data)
    await storage_writer(db)
    # Drain an in-flight remote write before cancellation releases the storage lock.
    return await storage_io(install_upload, normalized, owner_id, storage_root)
