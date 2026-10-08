import io
from pathlib import Path
from PIL import Image, PngImagePlugin
from app.design.manual import SaveManualRevision, save_manual
from app.design.models import Asset
from app.images import delivery
from app.images.delivery import locale_from, needs_watermark, watermarked_jpeg
from .test_migrations import migration_database
from .test_operations import factory
from .test_manual import base
from .test_api import api


def png(path: Path, *, text: str | None = None) -> Path:
    info = PngImagePlugin.PngInfo()
    if text:
        info.add_text("kigcraft.edit-recipe", text)
    Image.new("RGB", (640, 480), (40, 70, 100)).save(path, pnginfo=info)
    return path


def test_locale_follows_accept_language():
    assert locale_from("en-US,en;q=0.9") == "en"
    assert locale_from("ja") == "ja"
    assert locale_from("zh-TW,zh;q=0.8") == "zh-CN"
    assert locale_from(None) == locale_from("fr") == "zh-CN"


def test_rendition_is_watermarked_stripped_and_cached_without_touching_the_source(tmp_path):
    source = png(tmp_path / "master.png", text='{"secret":1}')
    before = source.read_bytes()
    data = watermarked_jpeg(source, key="unit:1", locale="en")
    assert source.read_bytes() == before
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (640, 480) and "kigcraft.edit-recipe" not in image.info
        marked = image.convert("RGB")
    with Image.open(source) as original:
        plain = original.convert("RGB")
    # The sponsor logo/line (lower left) and the AI notice (lower right) change those corners.
    for box in ((0, 400, 200, 480), (440, 400, 640, 480)):
        assert marked.crop(box).tobytes() != plain.crop(box).tobytes()
    assert watermarked_jpeg(source, key="unit:1", locale="en") is data
    assert watermarked_jpeg(source, key="unit:1", locale="ja") != data
    assert ("unit:1", "en", delivery.WATERMARK_VERSION, None) in delivery._cache


async def test_only_ai_output_and_edits_of_it_are_watermarked(factory, tmp_path):
    owner, branch, original = await base(factory, tmp_path)
    async with factory.begin() as db:
        master = await db.get(Asset, original.clean_asset_id)
        upload = Asset(owner_id=owner, object_key="upload.png", sha256="b" * 64, mime_type="image/png",
                       kind="reference", width=master.width, height=master.height, byte_size=1, status="ready")
        unrelated = Asset(owner_id=owner, object_key="photo.png", sha256="c" * 64, mime_type="image/png",
                          kind="reference", width=16, height=16, byte_size=1, status="ready")
        db.add_all([upload, unrelated]); await db.flush()
        assert master.kind == "master" and await needs_watermark(db, master)
        assert not await needs_watermark(db, upload) and not await needs_watermark(db, unrelated)
        await save_manual(db, owner, branch, SaveManualRevision(client_request_id="manual-watermark",
            base_revision_id=original.id, expected_branch_version=1, clean_asset_id=upload.id,
            recipe={"version": 1, "faceWidth": 0.2}))
    async with factory() as db:
        # A saved edit of AI output is still AI output; the user's own photo is not.
        assert await needs_watermark(db, await db.get(Asset, upload.id))
        assert not await needs_watermark(db, await db.get(Asset, unrelated.id))


async def test_user_uploads_are_returned_unchanged(api, tmp_path):
    client, *_ = api
    upload = await client.post("/api/v2/assets/uploads", files={"file": ("ref.png", png(tmp_path / "ref.png").read_bytes(), "image/png")})
    assert upload.status_code == 200
    served = await client.get(upload.json()["image_url"])
    assert served.status_code == 200 and "vary" not in served.headers
    with Image.open(io.BytesIO(served.content)) as image:
        # Uploads are re-encoded as JPEG: colours match within the codec's rounding.
        assert all(abs(a - b) <= 2 for a, b in zip(image.convert("RGB").getpixel((5, 475)), (40, 70, 100)))


async def test_pictures_are_cached_by_the_browser_and_revalidate_cheaply(api, tmp_path):
    client, factory, *_ = api
    upload = await client.post("/api/v2/assets/uploads", files={"file": ("ref.png", png(tmp_path / "ref.png").read_bytes(), "image/png")})
    url = upload.json()["image_url"]
    plain = await client.get(url)
    assert plain.headers["cache-control"] == "private, max-age=604800, immutable"
    again = await client.get(url, headers={"if-none-match": plain.headers["etag"]})
    assert again.status_code == 304
    async with factory.begin() as db:
        asset = await db.get(Asset, upload.json()["id"])
        asset.kind = "master"
    marked = await client.get(url, headers={"accept-language": "en"})
    assert marked.status_code == 200 and marked.headers["vary"] == "Accept-Language"
    assert marked.headers["cache-control"] == "private, max-age=604800, immutable"
    same = await client.get(url, headers={"accept-language": "en", "if-none-match": marked.headers["etag"]})
    assert same.status_code == 304 and not same.content
    other = await client.get(url, headers={"accept-language": "ja", "if-none-match": marked.headers["etag"]})
    assert other.status_code == 200 and other.headers["etag"] != marked.headers["etag"]


def test_a_version_and_date_label_is_printed_in_the_top_left_corner(tmp_path):
    source = png(tmp_path / "master.png")
    plain = watermarked_jpeg(source, key="label:0", locale="zh-CN")
    tagged = watermarked_jpeg(source, key="label:1", locale="zh-CN", label="v3 \u00b7 2026-10-04 15:45")
    with Image.open(io.BytesIO(plain)) as one, Image.open(io.BytesIO(tagged)) as two:
        corner = (0, 0, 300, 70)
        assert one.convert("RGB").crop(corner).tobytes() != two.convert("RGB").crop(corner).tobytes()
        # Nothing else moves: the label is a small plate, not a second tiled mark.
        far = (320, 150, 620, 260)
        assert one.convert("RGB").crop(far).tobytes() == two.convert("RGB").crop(far).tobytes()
    # The label is part of the cache key, so a plain copy is never served in place of a labelled one.
    assert watermarked_jpeg(source, key="label:1", locale="zh-CN", label="v3 \u00b7 2026-10-04 15:45") is tagged


async def test_the_label_is_only_given_for_the_owners_own_revision(factory, tmp_path):
    from uuid import uuid4
    owner, branch, revision = await base(factory, tmp_path)
    async with factory() as db:
        label = await delivery.version_label(db, owner, revision.clean_asset_id, revision.id)
        assert label and label.startswith(f"v{revision.number} \u00b7 20") and len(label) == len(f"v{revision.number} \u00b7 2026-10-04 15:45")
        assert await delivery.version_label(db, str(uuid4()), revision.clean_asset_id, revision.id) is None  # someone else's
        assert await delivery.version_label(db, owner, str(uuid4()), revision.id) is None  # not this revision's picture
        assert await delivery.version_label(db, owner, revision.clean_asset_id, None) is None
        assert await delivery.version_label(db, owner, revision.clean_asset_id, "not-a-revision") is None
