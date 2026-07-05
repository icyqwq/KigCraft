from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.generation.local_edit import (
    LocalEditValidationError,
    composite_local_edit,
    validate_local_edit_images,
)


def png_bytes(size: tuple[int, int], color: tuple[int, int, int, int]) -> bytes:
    image = Image.new("RGBA", size, color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def mask_bytes(size: tuple[int, int], box: tuple[int, int, int, int] | None) -> bytes:
    image = Image.new("L", size, 0)
    if box is not None:
        for y in range(box[1], box[3]):
            for x in range(box[0], box[2]):
                image.putpixel((x, y), 255)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_validate_local_edit_rejects_size_mismatch() -> None:
    with pytest.raises(LocalEditValidationError) as exc_info:
        validate_local_edit_images(
            png_bytes((4, 4), (10, 20, 30, 255)),
            mask_bytes((5, 4), (1, 1, 3, 3)),
        )

    assert exc_info.value.code == "local_revision_size_mismatch"


def test_validate_local_edit_rejects_empty_mask() -> None:
    with pytest.raises(LocalEditValidationError) as exc_info:
        validate_local_edit_images(
            png_bytes((4, 4), (10, 20, 30, 255)),
            mask_bytes((4, 4), None),
        )

    assert exc_info.value.code == "local_revision_mask_empty"


def test_validate_local_edit_rejects_transparent_rgba_mask() -> None:
    transparent_white_mask = Image.new("RGBA", (4, 4), (255, 255, 255, 0))
    buffer = BytesIO()
    transparent_white_mask.save(buffer, format="PNG")

    with pytest.raises(LocalEditValidationError) as exc_info:
        validate_local_edit_images(
            png_bytes((4, 4), (10, 20, 30, 255)),
            buffer.getvalue(),
        )

    assert exc_info.value.code == "local_revision_mask_empty"


def test_composite_keeps_mask_outside_pixels_identical(tmp_path) -> None:
    base_path = tmp_path / "base.png"
    mask_path = tmp_path / "mask.png"
    edited_path = tmp_path / "edited.png"
    output_path = tmp_path / "result.png"

    base_path.write_bytes(png_bytes((5, 5), (10, 20, 30, 255)))
    mask_path.write_bytes(mask_bytes((5, 5), (2, 2, 4, 4)))
    edited_path.write_bytes(png_bytes((5, 5), (200, 10, 10, 255)))

    info = composite_local_edit(base_path, mask_path, edited_path, output_path, feather_radius_px=2)

    assert (info.width, info.height) == (5, 5)
    result = Image.open(output_path).convert("RGBA")
    assert result.getpixel((0, 0)) == (10, 20, 30, 255)
    assert result.getpixel((1, 2)) == (10, 20, 30, 255)
    assert result.getpixel((2, 2)) != (10, 20, 30, 255)
