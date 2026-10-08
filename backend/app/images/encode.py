"""One JPEG encoding for everything the service stores or delivers (generated results, references, watermarked copies).

PNG is about four times larger for these pictures. Transparency is flattened onto white; metadata is dropped except an
embedded colour profile, which the caller may pass through.
"""
from __future__ import annotations

import io

from PIL import Image

QUALITY = 93
MIME = "image/jpeg"
SUFFIX = ".jpg"


def flatten(image: Image.Image) -> Image.Image:
    """RGB copy of the image; any alpha or palette transparency is composited over white."""
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


def to_jpeg(image: Image.Image, *, icc_profile: bytes | None = None) -> bytes:
    out = io.BytesIO()
    kwargs = {"icc_profile": icc_profile} if icc_profile else {}
    flatten(image).save(out, format="JPEG", quality=QUALITY, optimize=True, subsampling=0, **kwargs)
    return out.getvalue()
