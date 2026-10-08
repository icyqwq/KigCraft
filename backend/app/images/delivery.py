"""Watermarked delivery of AI-generated images.

Stored objects stay clean: generation, editing context and references read them from storage. Every copy that
leaves the server for a client (asset URLs, view-set downloads) carries the tiled domain and the AI-generated notice.
"""
from __future__ import annotations

import io
import threading
from collections import OrderedDict
from datetime import timedelta, timezone
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from app.design.models import Asset, Design, Revision
from app.images.encode import to_jpeg
from app.images.watermark import watermark_image

# Bump to invalidate cached renditions when the watermark design changes.
WATERMARK_VERSION = 2
GENERATED_KINDS = {"master", "legacy"}
_CACHE_BYTES = 96 * 1024 * 1024
_cache: OrderedDict[tuple, bytes] = OrderedDict()
_cache_size = 0
_lock = threading.Lock()


async def needs_watermark(db, asset: Asset) -> bool:
    """AI output, and edits saved on top of AI output; the user's own uploads and local imports stay as they are."""
    if asset.kind in GENERATED_KINDS:
        return True
    root_kind = await db.scalar(select(Asset.kind).join(Revision, Revision.root_source_asset_id == Asset.id).where(
        Revision.clean_asset_id == asset.id, Revision.source_kind == "manual").limit(1))
    return root_kind in GENERATED_KINDS


def locale_from(accept_language: str | None) -> str:
    for part in (accept_language or "").split(","):
        tag = part.split(";")[0].strip().lower()
        if tag.startswith("en"):
            return "en"
        if tag.startswith("ja"):
            return "ja"
        if tag.startswith("zh"):
            return "zh-CN"
    return "zh-CN"


def watermarked_jpeg(path: Path, *, key: str, locale: str, label: str | None = None) -> bytes:
    """JPEG bytes of the watermarked image; renditions are cached in memory only, never written to storage."""
    global _cache_size
    cache_key = (key, locale, WATERMARK_VERSION, label)
    with _lock:
        data = _cache.get(cache_key)
        if data is not None:
            _cache.move_to_end(cache_key)
            return data
    with Image.open(path) as source:
        source.load()
        icc = source.info.get("icc_profile")
        marked = watermark_image(source, locale=locale, label=label)
    # Re-encoding drops EXIF and text chunks such as hidden editing metadata.
    data = to_jpeg(marked, icc_profile=icc)
    with _lock:
        if cache_key not in _cache and len(data) <= _CACHE_BYTES // 4:
            _cache[cache_key] = data
            _cache_size += len(data)
            while _cache_size > _CACHE_BYTES:
                _, dropped = _cache.popitem(last=False)
                _cache_size -= len(dropped)
    return data


# The product's market is in China, which has no daylight saving: a fixed offset needs no timezone database in the image.
CHINA_TIME = timezone(timedelta(hours=8))


async def version_label(db, owner_id: str, asset_id: str, revision_id: str | None) -> str | None:
    """"v3 · 2026-10-04 15:45" for a picture the user owns, printed into the delivered copy; None when it cannot be vouched for."""
    if not revision_id:
        return None
    row = (await db.execute(select(Revision.number, Revision.created_at).join(Design, Design.id == Revision.design_id).where(
        Revision.id == revision_id, Revision.clean_asset_id == asset_id, Design.owner_id == owner_id))).first()
    if row is None or row[0] is None or row[1] is None:
        return None
    return f"v{row[0]} · {row[1].astimezone(CHINA_TIME):%Y-%m-%d %H:%M}"
