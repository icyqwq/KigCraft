from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter


MAX_LOCAL_EDIT_SIDE = 4096


class LocalEditValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LocalEditImageInfo:
    width: int
    height: int
    mask_non_empty: bool


def validate_local_edit_images(base_bytes: bytes, mask_bytes: bytes) -> LocalEditImageInfo:
    try:
        base = Image.open(BytesIO(base_bytes))
        base.load()
    except Exception as exc:
        raise LocalEditValidationError("local_revision_base_invalid") from exc

    try:
        mask = Image.open(BytesIO(mask_bytes))
        mask.load()
    except Exception as exc:
        raise LocalEditValidationError("local_revision_mask_invalid") from exc

    width, height = base.size
    if width <= 0 or height <= 0 or width > MAX_LOCAL_EDIT_SIDE or height > MAX_LOCAL_EDIT_SIDE:
        raise LocalEditValidationError("local_revision_base_invalid")
    if mask.size != base.size:
        raise LocalEditValidationError("local_revision_size_mismatch")

    alpha = _mask_to_alpha(mask)
    if alpha.getbbox() is None:
        raise LocalEditValidationError("local_revision_mask_empty")

    return LocalEditImageInfo(width=width, height=height, mask_non_empty=True)


def save_local_edit_inputs(base_bytes: bytes, mask_bytes: bytes, root: Path) -> LocalEditImageInfo:
    info = validate_local_edit_images(base_bytes, mask_bytes)
    root.mkdir(parents=True, exist_ok=True)
    base = Image.open(BytesIO(base_bytes)).convert("RGBA")
    mask = _mask_to_alpha(Image.open(BytesIO(mask_bytes)))
    base.save(root / "base.png", format="PNG")
    mask.save(root / "mask.png", format="PNG")
    return info


def composite_local_edit(
    base_path: Path,
    mask_path: Path,
    edited_path: Path,
    output_path: Path,
    feather_radius_px: int = 6,
) -> LocalEditImageInfo:
    try:
        base = Image.open(base_path).convert("RGBA")
    except Exception as exc:
        raise LocalEditValidationError("local_revision_base_invalid") from exc

    try:
        mask_image = Image.open(mask_path)
        mask_image.load()
    except Exception as exc:
        raise LocalEditValidationError("local_revision_mask_invalid") from exc

    try:
        edited = Image.open(edited_path).convert("RGBA")
    except Exception as exc:
        raise LocalEditValidationError("local_revision_output_invalid") from exc

    if mask_image.size != base.size:
        raise LocalEditValidationError("local_revision_size_mismatch")
    if edited.size != base.size:
        raise LocalEditValidationError("local_revision_output_size_mismatch")

    binary_mask = _mask_to_alpha(mask_image).point(
        lambda value: 255 if value > 0 else 0,
        mode="L",
    )
    if binary_mask.getbbox() is None:
        raise LocalEditValidationError("local_revision_mask_empty")

    radius = max(0, int(feather_radius_px))
    alpha = binary_mask.filter(ImageFilter.GaussianBlur(radius)) if radius else binary_mask
    alpha = ImageChops.multiply(alpha, binary_mask)

    result = Image.composite(edited, base, alpha)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.convert("RGB").save(output_path, format=_format_for_path(output_path), quality=95)
    return LocalEditImageInfo(width=base.width, height=base.height, mask_non_empty=True)


def _mask_to_alpha(mask: Image.Image) -> Image.Image:
    if mask.mode == "RGBA":
        return mask.getchannel("A")
    return mask.convert("L")


def _format_for_path(path: Path) -> str:
    return "WEBP" if path.suffix.lower() == ".webp" else "PNG"
