import hashlib
import io
from pathlib import Path
import pytest
from PIL import Image, ImageChops, ImageDraw
from app.codex_runtime.artifacts import ArtifactError, StagedImage
from app.operations.local_images import composite_locked, decode, mask_alpha, validate_pair

def png(image):
    stream = io.BytesIO(); image.save(stream, format="PNG"); return stream.getvalue()

def inputs(size=160):
    base = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(base)
    for y in range(0, size, 8):
        for x in range(0, size, 8):
            draw.rectangle((x,y,x+7,y+7), fill=(70,130,180,120) if (x+y)//8%2 else (200,100,60,255))
    mask = Image.new("RGBA", base.size, (255,255,255,0))
    draw = ImageDraw.Draw(mask)
    draw.rectangle((60,60,99,99), fill=(255,255,255,255))
    draw.rectangle((59,59,100,100), outline=(255,255,255,128))
    output = base.copy()
    draw = ImageDraw.Draw(output); draw.rectangle((59,59,100,100), fill=(20,240,50,200))
    return base, mask, output

def staged(tmp_path, image):
    data = png(image); target=tmp_path/"raw.png"; target.write_bytes(data)
    return StagedImage("raw", target, hashlib.sha256(data).hexdigest(), image.width, image.height, hashlib.sha256(data).hexdigest())

def test_locked_output_preserves_all_outside_rgba_pixels_and_replays_identically(tmp_path):
    base, mask, output = inputs()
    base.info["icc_profile"] = b"preserved-base-profile"
    # Small photometric noise outside is tolerated for registration, then removed by exact compositing.
    output = Image.eval(output, lambda value: min(255, value+2))
    raw = staged(tmp_path, output)
    first = composite_locked(png(base), png(mask), raw, tmp_path, "input")
    second = composite_locked(png(base), png(mask), raw, tmp_path, "input")
    assert first == second and first.local_input_digest == "input"
    result = decode(first.path.read_bytes()).convert("RGBA")
    assert result.info["icc_profile"] == base.info["icc_profile"]
    outside = ImageChops.invert(mask_alpha(mask).point(lambda value: 255 if value else 0))
    for channel in ImageChops.difference(base, result).split():
        assert ImageChops.multiply(channel, outside).getbbox() is None
    assert result.getpixel((70,70)) == output.getpixel((70,70))
    assert result.getpixel((59,59)) != output.getpixel((59,59))
    assert result.getpixel((59,59)) != base.getpixel((59,59))

@pytest.mark.parametrize("change", ["resize", "translate", "rotate", "rerender"])
def test_locked_output_rejects_unregistered_image_without_creating_delivery(tmp_path, change):
    base, mask, output = inputs()
    if change == "resize": output=output.resize((180,160))
    elif change == "translate": output=ImageChops.offset(output, 5, 0)
    elif change == "rotate": output=output.rotate(10)
    else: output=Image.new("RGBA",base.size,"blue")
    raw=staged(tmp_path,output)
    with pytest.raises(ArtifactError, match="local_(output_size_mismatch|alignment_unverified)"):
        composite_locked(png(base),png(mask),raw,tmp_path,"input")
    assert list(tmp_path.glob("*.png")) == [raw.path]

def test_ambiguous_uniform_background_cannot_claim_alignment(tmp_path):
    base=Image.new("RGBA",(160,160),"white")
    _,mask,output=inputs()
    with pytest.raises(ArtifactError,match="local_alignment_unverified"):
        composite_locked(png(base),png(mask),staged(tmp_path,output),tmp_path,"input")

def test_empty_and_mismatched_masks_are_rejected():
    base,mask,_=inputs()
    with pytest.raises(ArtifactError,match="local_mask_empty"):
        validate_pair(base,Image.new("RGBA",base.size))
    with pytest.raises(ArtifactError,match="local_mask_size_mismatch"):
        validate_pair(base,mask.resize((80,80)))

def test_same_native_identity_cannot_rewrite_a_processed_delivery(tmp_path):
    base,mask,output=inputs()
    raw=staged(tmp_path,output)
    first=composite_locked(png(base),png(mask),raw,tmp_path,"input")
    output.putpixel((70,70),(1,2,3,255))
    replacement=staged(tmp_path,output)
    with pytest.raises(ArtifactError,match="delivery_identity_conflict"):
        composite_locked(png(base),png(mask),replacement,tmp_path,"input")
    assert hashlib.sha256(first.path.read_bytes()).hexdigest()==first.sha256


@pytest.mark.parametrize("change", ["translate", "rotate", "scale", "rerender"])
@pytest.mark.parametrize("corner", [(210, 210), (120, 120)])
def test_small_outside_changes_cannot_hide_in_whole_image_average(tmp_path, change, corner):
    base, mask, output = inputs(size=512)
    x, y = corner
    box = (x, y, x + 24, y + 24)
    patch = base.crop(box)
    if change == "translate":
        patch = base.crop((x - 2, y, x + 22, y + 24))
    elif change == "rotate":
        patch = patch.rotate(12, resample=Image.Resampling.BICUBIC)
    elif change == "scale":
        patch = patch.resize((30, 30), resample=Image.Resampling.BICUBIC).crop((3, 3, 27, 27))
    else:
        patch = Image.new("RGBA", (24, 24), (0, 220, 0, 255))
    output.paste(patch, box)
    raw = staged(tmp_path, output)
    with pytest.raises(ArtifactError, match="local_alignment_unverified"):
        composite_locked(png(base), png(mask), raw, tmp_path, "input")
    assert list(tmp_path.glob("*.png")) == [raw.path]


def test_local_comparison_accepts_small_noise_with_partial_and_thin_outside_tiles(tmp_path):
    base, mask, output = inputs(size=257)
    # A large allowed region leaves partial tiles and a narrow outer band.
    mask = Image.new("RGBA", base.size, (255, 255, 255, 0))
    ImageDraw.Draw(mask).rectangle((8, 8, 240, 230), fill=(255, 255, 255, 255))
    output = Image.eval(base, lambda value: min(255, value + 2))
    ImageDraw.Draw(output).rectangle((8, 8, 240, 230), fill=(20, 240, 50, 200))
    result = composite_locked(png(base), png(mask), staged(tmp_path, output), tmp_path, "input")
    actual = decode(result.path.read_bytes()).convert("RGBA")
    outside = ImageChops.invert(mask_alpha(mask).point(lambda v: 255 if v else 0))
    for channel in ImageChops.difference(actual, base).split():
        assert ImageChops.multiply(channel, outside).getbbox() is None
