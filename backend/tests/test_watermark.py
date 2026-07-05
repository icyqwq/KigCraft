import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops

from app.images.watermark import apply_kigcraft_watermark


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


def test_watermark_cli_can_write_output_image(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "tools" / "watermark_image.py"
    source = tmp_path / "source.png"
    output = tmp_path / "watermarked.png"
    Image.new("RGB", (640, 480), (24, 32, 44)).save(source)

    result = subprocess.run(
        [sys.executable, str(script), str(source), "--output", str(output), "--force"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert str(output) in result.stdout
    assert output.is_file()
    assert output.with_name(f"{output.name}.watermarked").is_file()
    assert _has_rgb_difference(
        Image.open(source).convert("RGBA"),
        Image.open(output).convert("RGBA"),
    )


def _has_rgb_difference(before: Image.Image, after: Image.Image) -> bool:
    return ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox() is not None
