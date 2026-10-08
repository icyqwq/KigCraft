"""Source-sized local masks and conservative registration checks; never resize AI output."""
from __future__ import annotations
import hashlib
import io
import os
import tempfile
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from app.codex_runtime.artifacts import ArtifactError, StagedImage

MAX_PIXELS = 25_000_000

def decode(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_PIXELS or getattr(source, "n_frames", 1) != 1:
                raise ArtifactError("local_image_invalid")
            source.load()
            return source.copy()
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ArtifactError("local_image_invalid") from error

def mask_alpha(mask: Image.Image) -> Image.Image:
    return mask.getchannel("A") if "A" in mask.getbands() else mask.convert("L")

def validate_pair(base: Image.Image, mask: Image.Image) -> Image.Image:
    if base.size != mask.size:
        raise ArtifactError("local_mask_size_mismatch")
    alpha = mask_alpha(mask)
    if not alpha.getbbox():
        raise ArtifactError("local_mask_empty")
    return alpha

def anchor_area(base: Image.Image, alpha: Image.Image) -> tuple[Image.Image, int]:
    # Exclude selection boundaries and image borders; alpha==0 is the only unselected region.
    inside = alpha.point(lambda v: 255 if v else 0).filter(ImageFilter.MaxFilter(7))
    outside = ImageChops.invert(inside)
    ImageDraw.Draw(outside).rectangle((0, 0, base.width-1, base.height-1), outline=0, width=2)
    count = outside.histogram()[255]
    if count < max(256, base.width * base.height // 10):
        raise ArtifactError("local_alignment_unverified")
    # Uniform backgrounds cannot prove registration. Require distributed visible edge anchors.
    edges = base.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 16 else 0)
    anchors = ImageChops.multiply(edges, outside)
    quadrants = [(0,0,base.width//2,base.height//2), (base.width//2,0,base.width,base.height//2),
                 (0,base.height//2,base.width//2,base.height), (base.width//2,base.height//2,base.width,base.height)]
    if sum(anchors.crop(box).histogram()[255] >= 16 for box in quadrants) < 3:
        raise ArtifactError("local_alignment_unverified")
    return outside, count

def verify_registration(base: Image.Image, edited: Image.Image, alpha: Image.Image):
    if base.size != edited.size:
        raise ArtifactError("local_output_size_mismatch")
    outside, count = anchor_area(base, alpha)
    difference = ImageChops.difference(base.convert("RGBA"), edited.convert("RGBA"))
    channels = difference.split()
    maximum = channels[0]
    for channel in channels[1:]:
        maximum = ImageChops.lighter(maximum, channel)
    difference_outside = ImageChops.multiply(maximum, outside)

    def check(histogram, pixels):
        # Substantial re-rendering is not evidence of alignment, globally or locally.
        if sum(value * amount for value, amount in enumerate(histogram)) / pixels > 3 or sum(histogram[17:]) / pixels > .01:
            raise ArtifactError("local_alignment_unverified")

    check(difference_outside.histogram(), count)
    # Whole-image averages can hide a displaced feature in an otherwise unchanged image.
    # Overlap the source-pixel windows so a change cannot hide across a tile boundary.
    # Ignore tiny exterior fragments; neighboring overlapping windows still cover them
    # when enough unselected pixels exist to make a local comparison meaningful.
    for top in range(0, base.height, 32):
        for left in range(0, base.width, 32):
            box = (left, top, min(left + 64, base.width), min(top + 64, base.height))
            pixels = outside.crop(box).histogram()[255]
            if pixels >= 64:
                check(difference_outside.crop(box).histogram(), pixels)

def composite_locked(base_data: bytes, mask_data: bytes, edited: StagedImage, staging_root: Path,
                     input_digest: str) -> StagedImage:
    if hashlib.sha256(edited.path.read_bytes()).hexdigest() != edited.sha256:
        raise ArtifactError("staged_image_changed")
    base, mask, output = decode(base_data), decode(mask_data), decode(edited.path.read_bytes())
    alpha = validate_pair(base, mask)
    verify_registration(base, output, alpha)
    # Respect the exact visible alpha, including antialiasing; no outward feather or RGB/alpha flattening.
    result = Image.composite(output.convert("RGBA"), base.convert("RGBA"), alpha)
    buffer = io.BytesIO()
    result.save(buffer, format="PNG", icc_profile=base.info.get("icc_profile"))
    data = buffer.getvalue()
    checksum = hashlib.sha256(data).hexdigest()
    identity = hashlib.sha256(("local-lock-v1:" + edited.delivery_key + ":" + input_digest).encode()).hexdigest()
    target = staging_root / (identity + ".png")
    staging_root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".local-", dir=staging_root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                raise ArtifactError("delivery_identity_conflict") from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return StagedImage(identity, target, checksum, base.width, base.height, edited.source_sha256, input_digest)


ASPECT_TOLERANCE = 0.03

def conform_to_base(base_data: bytes, edited: StagedImage, staging_root: Path, input_digest: str) -> StagedImage:
    """The editor adopts the result as its new working image, so it must have the base's exact size.

    Image tools return a few fixed sizes. A result whose proportions match the base is scaled to the base's size; anything
    else is refused rather than stretched.
    """
    if hashlib.sha256(edited.path.read_bytes()).hexdigest() != edited.sha256:
        raise ArtifactError("staged_image_changed")
    base, output = decode(base_data), decode(edited.path.read_bytes())
    if output.size == base.size:
        return edited
    if abs((output.width / output.height) / (base.width / base.height) - 1) > ASPECT_TOLERANCE:
        raise ArtifactError("local_output_size_mismatch")
    from app.images.encode import SUFFIX, to_jpeg
    data = to_jpeg(output.resize(base.size, Image.LANCZOS), icc_profile=base.info.get("icc_profile"))
    checksum = hashlib.sha256(data).hexdigest()
    identity = hashlib.sha256(("local-fit-v1:" + edited.delivery_key + ":" + input_digest).encode()).hexdigest()
    target = staging_root / (identity + SUFFIX)
    staging_root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".local-", dir=staging_root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                raise ArtifactError("delivery_identity_conflict") from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return StagedImage(identity, target, checksum, base.width, base.height, edited.source_sha256, input_digest)
