import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from app.core.config import get_settings
from app.core.paths import resolve_repo_path

logger = logging.getLogger("uvicorn.error")

WATERMARK_TEXT = "KigCraft AI generated"
WATERMARK_DOMAIN_TEXT = "KigCraft"
WATERMARK_ADD_CORNER_LOGO = True
WATERMARK_ADD_TILED_DOMAIN = True

# Tiled diagonal domain watermark.
WATERMARK_DENSITY_MULTIPLIER = 1.0
WATERMARK_TILED_FONT_MIN_SIZE = 18
WATERMARK_TILED_FONT_SHORT_SIDE_DIVISOR = 24
WATERMARK_TILED_BASE_TILE_MIN_SIZE = 180
WATERMARK_TILED_BASE_TILE_SHORT_SIDE_DIVISOR = 3
WATERMARK_TILED_TILE_MIN_SIZE = 120
WATERMARK_TILED_ROTATION_DEGREES = -32
WATERMARK_TILED_LIGHT_COLOR = (255, 255, 255)
WATERMARK_TILED_LIGHT_ALPHA = 0
WATERMARK_TILED_SHADOW_COLOR = (0, 0, 0)
WATERMARK_TILED_SHADOW_ALPHA = 16

# Bottom-left logo and text watermark.
WATERMARK_CORNER_MARGIN_RATIO = 0.035
WATERMARK_CORNER_MARGIN_MIN = 12
WATERMARK_CORNER_GAP_RATIO = 0.012
WATERMARK_CORNER_GAP_MIN = 6
WATERMARK_CORNER_LOGO_WIDTH_RATIO = 0.22
WATERMARK_CORNER_LOGO_WIDTH_MIN = 72
WATERMARK_CORNER_LOGO_OPACITY = 0.88
WATERMARK_CORNER_TEXT_FONT_RATIO = 0.028
WATERMARK_CORNER_TEXT_FONT_MIN_SIZE = 14
WATERMARK_CORNER_TEXT_COLOR = (255, 255, 255)
WATERMARK_CORNER_TEXT_ALPHA = 238
WATERMARK_CORNER_TEXT_SHADOW_COLOR = (0, 0, 0)
WATERMARK_CORNER_TEXT_SHADOW_ALPHA = 170
WATERMARK_CORNER_TEXT_SHADOW_OFFSET_RATIO = 0.08
WATERMARK_CORNER_TEXT_SHADOW_OFFSET_MIN = 1
# DEFAULT_WATERMARK_LOGO_PATH = resolve_repo_path("ref/moyulogo.png")
DEFAULT_WATERMARK_LOGO_PATH = ''

def apply_kigcraft_watermark(
    image_path: Path,
    text: str = WATERMARK_TEXT,
    *,
    force: bool = False,
    logo_path: Path | None = None,
) -> bool:
    settings = get_settings()
    effective_text = text if text != WATERMARK_TEXT else settings.watermark_text
    effective_domain_text = settings.watermark_domain_text or WATERMARK_DOMAIN_TEXT

    if not force and _is_currently_watermarked(image_path):
        return True

    try:
        with Image.open(image_path) as source:
            image = source.convert("RGBA")
            fmt = source.format
            exif = source.info.get("exif")
    except (OSError, UnidentifiedImageError) as exc:
        logger.warning("Skipping watermark for unreadable image path=%s error=%s", image_path, exc)
        return False

    width, height = image.size
    if width <= 0 or height <= 0:
        return False

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    if WATERMARK_ADD_TILED_DOMAIN:
        _draw_tiled_domain_watermark(overlay, image.size, text=effective_domain_text)
    corner_logo_path = logo_path if logo_path is not None else DEFAULT_WATERMARK_LOGO_PATH
    _draw_corner_watermark(overlay, image.size, text=effective_text, logo_path=corner_logo_path)

    watermarked = Image.alpha_composite(image, overlay)
    _save_image(watermarked, image_path, fmt, exif)
    _write_watermark_marker(image_path)
    return True


def _load_font(size: int) -> ImageFont.ImageFont:
    for name in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "msyh.ttc",
        "simhei.ttf",
        "NotoSansCJK-Regular.ttc",
        "NotoSansSC-Regular.otf",
        "SourceHanSansSC-Regular.otf",
        "DejaVuSans-Bold.ttf",
        "arial.ttf",
    ):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_tiled_domain_watermark(
    overlay: Image.Image,
    image_size: tuple[int, int],
    text: str = WATERMARK_DOMAIN_TEXT,
) -> None:
    width, height = image_size
    base_tile_size = max(
        WATERMARK_TILED_BASE_TILE_MIN_SIZE,
        min(width, height) // WATERMARK_TILED_BASE_TILE_SHORT_SIDE_DIVISOR,
    )
    tile_size = max(120, round(base_tile_size / math.sqrt(WATERMARK_DENSITY_MULTIPLIER)))
    tile_size = max(WATERMARK_TILED_TILE_MIN_SIZE, tile_size)
    font = _load_font(
        max(WATERMARK_TILED_FONT_MIN_SIZE, min(width, height) // WATERMARK_TILED_FONT_SHORT_SIDE_DIVISOR)
    )
    text_layer_size = int(math.hypot(width, height)) + tile_size * 2
    text_layer = Image.new("RGBA", (text_layer_size, text_layer_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)

    for y in range(-tile_size, text_layer_size + tile_size, tile_size):
        for x in range(-tile_size, text_layer_size + tile_size, tile_size):
            draw.text(
                (x, y),
                text,
                fill=(*WATERMARK_TILED_LIGHT_COLOR, WATERMARK_TILED_LIGHT_ALPHA),
                font=font,
            )
            draw.text(
                (x + 1, y + 1),
                text,
                fill=(*WATERMARK_TILED_SHADOW_COLOR, WATERMARK_TILED_SHADOW_ALPHA),
                font=font,
            )

    rotated = text_layer.rotate(
        WATERMARK_TILED_ROTATION_DEGREES,
        expand=False,
        resample=Image.Resampling.BICUBIC,
    )
    offset = ((width - text_layer_size) // 2, (height - text_layer_size) // 2)
    overlay.alpha_composite(rotated, dest=offset)


def _draw_corner_watermark(
    overlay: Image.Image,
    image_size: tuple[int, int],
    *,
    logo_path: Path | str | None,
    text: str,
) -> None:
    width, height = image_size
    short_side = max(1, min(width, height))
    margin = max(WATERMARK_CORNER_MARGIN_MIN, round(short_side * WATERMARK_CORNER_MARGIN_RATIO))
    gap = max(WATERMARK_CORNER_GAP_MIN, round(short_side * WATERMARK_CORNER_GAP_RATIO))
    logo_width = max(
        WATERMARK_CORNER_LOGO_WIDTH_MIN,
        round(short_side * WATERMARK_CORNER_LOGO_WIDTH_RATIO),
    )
    font_size = max(
        WATERMARK_CORNER_TEXT_FONT_MIN_SIZE,
        round(short_side * WATERMARK_CORNER_TEXT_FONT_RATIO),
    )
    shadow_offset = max(
        WATERMARK_CORNER_TEXT_SHADOW_OFFSET_MIN,
        round(font_size * WATERMARK_CORNER_TEXT_SHADOW_OFFSET_RATIO),
    )

    logo = _load_logo(logo_path, logo_width) if WATERMARK_ADD_CORNER_LOGO and logo_path else None
    font = _load_font(font_size)
    draw = ImageDraw.Draw(overlay)
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_height = text_bbox[3] - text_bbox[1]

    logo_height = logo.height if logo else 0
    group_height = logo_height + (gap if logo else 0) + text_height
    left = margin
    top = max(margin, height - margin - group_height)

    current_y = top
    if logo:
        overlay.alpha_composite(_apply_opacity(logo, WATERMARK_CORNER_LOGO_OPACITY), dest=(left, current_y))
        current_y += logo.height + gap

    text_x = left
    text_y = current_y - text_bbox[1]
    draw.text(
        (text_x + shadow_offset, text_y + shadow_offset),
        text,
        fill=(*WATERMARK_CORNER_TEXT_SHADOW_COLOR, WATERMARK_CORNER_TEXT_SHADOW_ALPHA),
        font=font,
    )
    draw.text(
        (text_x, text_y),
        text,
        fill=(*WATERMARK_CORNER_TEXT_COLOR, WATERMARK_CORNER_TEXT_ALPHA),
        font=font,
    )


def _load_logo(logo_path: Path | str, target_width: int) -> Image.Image | None:
    logo_path = Path(logo_path)
    try:
        with Image.open(logo_path) as source:
            logo = source.convert("RGBA")
    except (OSError, UnidentifiedImageError) as exc:
        logger.warning("Skipping watermark logo path=%s error=%s", logo_path, exc)
        return None

    if logo.width <= 0 or logo.height <= 0:
        return None

    target_height = max(1, round(target_width * logo.height / logo.width))
    return logo.resize((target_width, target_height), Image.Resampling.LANCZOS)


def _apply_opacity(image: Image.Image, opacity: float) -> Image.Image:
    output = image.copy()
    alpha = output.getchannel("A")
    output.putalpha(alpha.point(lambda value: round(value * opacity)))
    return output


def _save_image(image: Image.Image, image_path: Path, fmt: str | None, exif: bytes | None) -> None:
    suffix = image_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"} or fmt == "JPEG":
        rgb = image.convert("RGB")
        save_kwargs = {"quality": 94, "optimize": True}
        if exif:
            save_kwargs["exif"] = exif
        rgb.save(image_path, format="JPEG", **save_kwargs)
        return
    if suffix == ".webp" or fmt == "WEBP":
        image.save(image_path, format="WEBP", quality=94, method=6)
        return
    image.save(image_path, format="PNG", optimize=True)


def _is_currently_watermarked(image_path: Path) -> bool:
    marker = _marker_path(image_path)
    if not marker.is_file():
        return False
    try:
        return marker.read_text(encoding="utf-8") == _image_signature(image_path)
    except OSError:
        return False


def _write_watermark_marker(image_path: Path) -> None:
    marker = _marker_path(image_path)
    try:
        marker.write_text(_image_signature(image_path), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write watermark marker path=%s error=%s", marker, exc)


def _marker_path(image_path: Path) -> Path:
    return image_path.with_name(f"{image_path.name}.watermarked")


def _image_signature(image_path: Path) -> str:
    stat = image_path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"
