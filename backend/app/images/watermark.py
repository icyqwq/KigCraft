import logging
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


logger = logging.getLogger("uvicorn.error")

WATERMARK_DOMAIN_TEXT = "kigcraft.com"
WATERMARK_ADD_CORNER_LOGO = True
WATERMARK_ADD_TILED_DOMAIN = True


@dataclass(frozen=True)
class WatermarkTexts:
    brand: str
    sponsor: str
    disclaimer: str


WATERMARK_TEXTS = {
    "zh-CN": WatermarkTexts(
        brand="KigCraft",
        sponsor="",
        disclaimer="由AI生成，仅供参考",
    ),
    "en": WatermarkTexts(
        brand="KigCraft",
        sponsor="",
        disclaimer="AI-generated, for reference only",
    ),
    "ja": WatermarkTexts(
        brand="KigCraft",
        sponsor="",
        disclaimer="AI生成・参考用",
    ),
}

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

# Bottom-right disclaimer watermark.
WATERMARK_DISCLAIMER_FONT_RATIO = 0.024
WATERMARK_DISCLAIMER_FONT_MIN_SIZE = 13
WATERMARK_DISCLAIMER_COLOR = (255, 255, 255)
WATERMARK_DISCLAIMER_ALPHA = 220
WATERMARK_DISCLAIMER_SHADOW_COLOR = (0, 0, 0)
WATERMARK_DISCLAIMER_SHADOW_ALPHA = 150

# Packaged with the application so the API image (which has no ref/ folder) can draw it too.
DEFAULT_WATERMARK_LOGO_PATH = Path(__file__).with_name("assets") / "moyulogo.png"


def apply_kigcraft_watermark(
    image_path: Path,
    *,
    force: bool = False,
    locale: object = "zh-CN",
    logo_path: Path | None = None,
) -> bool:
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

    watermarked = watermark_image(image, locale=locale, logo_path=logo_path)
    _save_image(watermarked, image_path, fmt, exif)
    _write_watermark_marker(image_path)
    return True


def watermark_image(image: Image.Image, *, locale: object = "zh-CN", logo_path: Path | None = None,
                    label: str | None = None) -> Image.Image:
    """Return a copy of an RGBA image with the tiled domain and AI-generated notice.

    `label`, when given, is a small version/date tag drawn in the top-left corner."""
    image = image.convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    texts = get_watermark_texts(locale)
    if WATERMARK_ADD_TILED_DOMAIN:
        _draw_tiled_domain_watermark(overlay, image.size, texts=texts)
    bottom_edge = _corner_bottom_edge(image.size)
    _draw_bottom_right_watermark(
        overlay,
        image.size,
        bottom_edge=bottom_edge,
        disclaimer_text=texts.disclaimer,
    )

    if label:
        _draw_top_left_label(overlay, image.size, label)

    return Image.alpha_composite(image, overlay)


def _draw_top_left_label(overlay: Image.Image, image_size: tuple[int, int], label: str) -> None:
    """A small plate with the version and the time the picture was made."""
    short_side = max(1, min(image_size))
    margin = max(10, round(short_side * 0.02))
    font_size = max(12, round(short_side * 0.022))
    font = _load_font(font_size)
    draw = ImageDraw.Draw(overlay)
    left, top, right, bottom = _text_bbox(draw, label, font)
    pad = max(4, round(font_size * 0.4))
    box = (margin, margin, margin + (right - left) + 2 * pad, margin + (bottom - top) + 2 * pad)
    draw.rounded_rectangle(box, radius=max(3, pad // 2), fill=(0, 0, 0, 120))
    draw.text((margin + pad - left, margin + pad - top), label, font=font, fill=(255, 255, 255, 235))


def get_watermark_texts(locale: object) -> WatermarkTexts:
    normalized = str(locale or "").strip()
    if normalized.startswith("en-"):
        normalized = "en"
    elif normalized.startswith("ja-"):
        normalized = "ja"
    elif normalized.startswith("zh-"):
        normalized = "zh-CN"
    return WATERMARK_TEXTS.get(normalized, WATERMARK_TEXTS["zh-CN"])


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
    *,
    texts: WatermarkTexts,
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
    tiled_texts = (WATERMARK_DOMAIN_TEXT, WATERMARK_DOMAIN_TEXT)

    row_index = 0
    for y in range(-tile_size, text_layer_size + tile_size, tile_size):
        x_offset = tile_size // 2 if row_index % 2 else 0
        col_index = 0
        for x in range(-tile_size, text_layer_size + tile_size, tile_size):
            text = tiled_texts[(row_index + col_index) % 2]
            draw_x = x + x_offset
            draw.text(
                (draw_x, y),
                text,
                fill=(*WATERMARK_TILED_LIGHT_COLOR, WATERMARK_TILED_LIGHT_ALPHA),
                font=font,
            )
            draw.text(
                (draw_x + 1, y + 1),
                text,
                fill=(*WATERMARK_TILED_SHADOW_COLOR, WATERMARK_TILED_SHADOW_ALPHA),
                font=font,
            )
            col_index += 1
        row_index += 1

    rotated = text_layer.rotate(
        WATERMARK_TILED_ROTATION_DEGREES,
        expand=False,
        resample=Image.Resampling.BICUBIC,
    )
    offset = ((width - text_layer_size) // 2, (height - text_layer_size) // 2)
    overlay.alpha_composite(rotated, dest=offset)


def _corner_bottom_edge(image_size: tuple[int, int]) -> int:
    _width, height = image_size
    short_side = max(1, min(image_size[0], image_size[1]))
    margin = max(WATERMARK_CORNER_MARGIN_MIN, round(short_side * WATERMARK_CORNER_MARGIN_RATIO))
    return height - margin


def _corner_font_size(image_size: tuple[int, int]) -> int:
    short_side = max(1, min(image_size))
    return max(
        WATERMARK_CORNER_TEXT_FONT_MIN_SIZE,
        round(short_side * WATERMARK_CORNER_TEXT_FONT_RATIO),
    )


def _corner_shadow_offset(font_size: int) -> int:
    return max(
        WATERMARK_CORNER_TEXT_SHADOW_OFFSET_MIN,
        round(font_size * WATERMARK_CORNER_TEXT_SHADOW_OFFSET_RATIO),
    )


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=font)


def _draw_shadowed_text_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    text_color: tuple[int, int, int],
    text_alpha: int,
    shadow_color: tuple[int, int, int],
    shadow_alpha: int,
    shadow_offset: int,
) -> None:
    draw.text(
        (x + shadow_offset, y + shadow_offset),
        text,
        fill=(*shadow_color, shadow_alpha),
        font=font,
    )
    draw.text(
        (x, y),
        text,
        fill=(*text_color, text_alpha),
        font=font,
    )


def _draw_bottom_left_watermark(
    overlay: Image.Image,
    image_size: tuple[int, int],
    *,
    bottom_edge: int,
    logo_path: Path | str | None,
    sponsor_text: str,
) -> None:
    short_side = max(1, min(image_size))
    margin = max(WATERMARK_CORNER_MARGIN_MIN, round(short_side * WATERMARK_CORNER_MARGIN_RATIO))
    gap = max(WATERMARK_CORNER_GAP_MIN, round(short_side * WATERMARK_CORNER_GAP_RATIO))
    logo_width = max(
        WATERMARK_CORNER_LOGO_WIDTH_MIN,
        round(short_side * WATERMARK_CORNER_LOGO_WIDTH_RATIO),
    )
    font_size = _corner_font_size(image_size)
    shadow_offset = _corner_shadow_offset(font_size)
    font = _load_font(font_size)
    draw = ImageDraw.Draw(overlay)

    logo = _load_logo(logo_path, logo_width) if WATERMARK_ADD_CORNER_LOGO and logo_path else None
    sponsor_bbox = _text_bbox(draw, sponsor_text, font)
    sponsor_width = sponsor_bbox[2] - sponsor_bbox[0]
    sponsor_height = sponsor_bbox[3] - sponsor_bbox[1]
    logo_block_width = logo.width if logo else 0
    logo_block_height = logo.height if logo else 0
    group_width = max(logo_block_width, sponsor_width)
    group_height = logo_block_height + (gap if logo else 0) + sponsor_height
    group_top = bottom_edge - group_height

    if logo:
        logo_x = margin + (group_width - logo_block_width) // 2
        overlay.alpha_composite(_apply_opacity(logo, WATERMARK_CORNER_LOGO_OPACITY), dest=(logo_x, group_top))
        sponsor_y = group_top + logo_block_height + gap - sponsor_bbox[1]
    else:
        sponsor_y = bottom_edge - sponsor_bbox[3]

    sponsor_x = margin + (group_width - sponsor_width) // 2 - sponsor_bbox[0]
    _draw_shadowed_text_line(
        draw,
        sponsor_text,
        x=sponsor_x,
        y=sponsor_y,
        font=font,
        text_color=WATERMARK_CORNER_TEXT_COLOR,
        text_alpha=WATERMARK_CORNER_TEXT_ALPHA,
        shadow_color=WATERMARK_CORNER_TEXT_SHADOW_COLOR,
        shadow_alpha=WATERMARK_CORNER_TEXT_SHADOW_ALPHA,
        shadow_offset=shadow_offset,
    )


def _draw_bottom_right_watermark(
    overlay: Image.Image,
    image_size: tuple[int, int],
    *,
    bottom_edge: int,
    disclaimer_text: str,
) -> None:
    width, _height = image_size
    short_side = max(1, min(image_size))
    margin = max(WATERMARK_CORNER_MARGIN_MIN, round(short_side * WATERMARK_CORNER_MARGIN_RATIO))
    gap = max(WATERMARK_CORNER_GAP_MIN, round(short_side * WATERMARK_CORNER_GAP_RATIO))
    font_size = _corner_font_size(image_size)
    shadow_offset = _corner_shadow_offset(font_size)
    font = _load_font(font_size)
    draw = ImageDraw.Draw(overlay)
    right_edge = width - margin

    disclaimer_bbox = _text_bbox(draw, disclaimer_text, font)
    domain_bbox = _text_bbox(draw, WATERMARK_DOMAIN_TEXT, font)
    disclaimer_y = bottom_edge - disclaimer_bbox[3]
    domain_y = disclaimer_y - gap - (domain_bbox[3] - domain_bbox[1])

    _draw_shadowed_text_line(
        draw,
        WATERMARK_DOMAIN_TEXT,
        x=right_edge - domain_bbox[2],
        y=domain_y,
        font=font,
        text_color=WATERMARK_CORNER_TEXT_COLOR,
        text_alpha=WATERMARK_CORNER_TEXT_ALPHA,
        shadow_color=WATERMARK_CORNER_TEXT_SHADOW_COLOR,
        shadow_alpha=WATERMARK_CORNER_TEXT_SHADOW_ALPHA,
        shadow_offset=shadow_offset,
    )
    _draw_shadowed_text_line(
        draw,
        disclaimer_text,
        x=right_edge - disclaimer_bbox[2],
        y=disclaimer_y,
        font=font,
        text_color=WATERMARK_DISCLAIMER_COLOR,
        text_alpha=WATERMARK_DISCLAIMER_ALPHA,
        shadow_color=WATERMARK_DISCLAIMER_SHADOW_COLOR,
        shadow_alpha=WATERMARK_DISCLAIMER_SHADOW_ALPHA,
        shadow_offset=shadow_offset,
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
