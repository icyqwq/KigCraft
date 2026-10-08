"""Validate native imageGeneration results into private immutable JPEG staging.

This does not publish a revision or settle quota. A replay can restage the same
delivery without calling Codex again. Saved paths must be inside an operation's
fresh output directory; arbitrary model-provided paths and links are refused.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.images.encode import SUFFIX, to_jpeg

PNG_MAGIC = bytes([0x89]) + b"PNG"


class ArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class StagedImage:
    delivery_key: str
    path: Path
    sha256: str
    width: int
    height: int
    source_sha256: str
    local_input_digest: str | None = None


def _read_output(path: str, output_root: Path, max_bytes: int) -> bytes:
    root = output_root.resolve(strict=True)
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    # Check lexical containment and every component before following links.
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise ArtifactError("output_outside_operation") from None
    if ".." in relative.parts:
        raise ArtifactError("output_outside_operation")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ArtifactError("linked_output")
    if not candidate.resolve(strict=True).is_relative_to(root):
        raise ArtifactError("output_outside_operation")
    with candidate.open("rb") as source:
        data = source.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ArtifactError("image_too_large")
    return data


def _jpeg(data: bytes, max_pixels: int) -> tuple[bytes, int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise ArtifactError("image_dimensions_exceeded")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                if getattr(image, "n_frames", 1) != 1:
                    raise ArtifactError("animated_output")
                # Strip untrusted metadata. A normal opaque result becomes one compact JPEG; a result that really has
                # transparency stays lossless PNG rather than being flattened.
                if "A" in image.getbands() or "transparency" in image.info:
                    low, high = image.convert("RGBA").getchannel("A").getextrema()
                    if high == 0:
                        raise ArtifactError("empty_image")
                    if low < 255:
                        target = io.BytesIO()
                        image.convert("RGBA").save(target, format="PNG")
                        return target.getvalue(), width, height
                return to_jpeg(image), width, height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ArtifactError("invalid_image") from None


def stage_image(
    item: dict[str, Any], *, runtime_id: str, thread_id: str, turn_id: str,
    output_root: Path, staging_root: Path, max_bytes: int = 20 * 1024 * 1024,
    max_pixels: int = 25_000_000,
) -> StagedImage:
    if item.get("type") != "imageGeneration" or item.get("status") != "completed" or item.get("failure"):
        raise ArtifactError("not_completed_image_generation")
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id or not all((runtime_id, thread_id, turn_id)):
        raise ArtifactError("missing_delivery_identity")
    identity = "\0".join((runtime_id, thread_id, turn_id, item_id))
    delivery_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    result = item.get("result")
    if isinstance(result, str) and result:
        if result.startswith("data:"):
            header, separator, result = result.partition(",")
            if not separator or header not in {"data:image/png;base64", "data:image/webp;base64", "data:image/jpeg;base64"}:
                raise ArtifactError("unsupported_image_encoding")
        if len(result) > ((max_bytes + 2) // 3) * 4:
            raise ArtifactError("image_too_large")
        try:
            data = base64.b64decode(result, validate=True)
        except (binascii.Error, ValueError):
            raise ArtifactError("invalid_image_encoding") from None
    elif isinstance(item.get("savedPath"), str):
        try:
            data = _read_output(item["savedPath"], output_root, max_bytes)
        except OSError:
            raise ArtifactError("output_unavailable") from None
    else:
        raise ArtifactError("missing_image_result")
    if len(data) > max_bytes:
        raise ArtifactError("image_too_large")
    jpeg, width, height = _jpeg(data, max_pixels)
    suffix = ".png" if jpeg.startswith(PNG_MAGIC) else SUFFIX
    staging_root.mkdir(parents=True, exist_ok=True)
    target = staging_root / f"{delivery_key}{suffix}"
    digest = hashlib.sha256(jpeg).hexdigest()
    fd, temporary = tempfile.mkstemp(prefix=".staging-", dir=staging_root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(jpeg)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)  # atomic no-overwrite publication within private staging
        except FileExistsError:
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ArtifactError("delivery_identity_conflict") from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return StagedImage(delivery_key, target, digest, width, height, hashlib.sha256(data).hexdigest())
