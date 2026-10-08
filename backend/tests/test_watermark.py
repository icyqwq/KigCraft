import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from app.images.watermark import WatermarkTexts, apply_kigcraft_watermark, get_watermark_texts


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("zh-CN", WatermarkTexts("KigCraft", "", "由AI生成，仅供参考")),
        ("en", WatermarkTexts("KigCraft", "", "AI-generated, for reference only")),
        ("ja", WatermarkTexts("KigCraft", "", "AI生成・参考用")),
        ("en-US", WatermarkTexts("KigCraft", "", "AI-generated, for reference only")),
        ("unsupported", WatermarkTexts("KigCraft", "", "由AI生成，仅供参考")),
    ],
)
def test_watermark_texts_follow_normalized_locale(locale: str, expected: WatermarkTexts) -> None:
    assert get_watermark_texts(locale) == expected


def test_kigcraft_watermark_marks_bottom_left_and_writes_marker(tmp_path: Path) -> None:
    image_path = tmp_path / "candidate.png"
    Image.new("RGB", (800, 600), (42, 68, 96)).save(image_path)
    before = Image.open(image_path).convert("RGBA")

    assert apply_kigcraft_watermark(image_path, force=True)

    after = Image.open(image_path).convert("RGBA")
    bottom_left_before = before.crop((0, 420, 280, 600))
    bottom_left_after = after.crop((0, 420, 280, 600))
    center_before = before.crop((300, 220, 500, 380))
    center_after = after.crop((300, 220, 500, 380))
    assert _has_rgb_difference(bottom_left_before, bottom_left_after)
    assert _has_rgb_difference(center_before, center_after)
    assert image_path.with_name(f"{image_path.name}.watermarked").is_file()



def _has_rgb_difference(before: Image.Image, after: Image.Image) -> bool:
    return ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox() is not None
