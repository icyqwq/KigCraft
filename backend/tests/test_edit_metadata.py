import json
from pathlib import Path

from PIL import Image

from app.images.edit_metadata import embed_edit_recipe_in_png


def test_embed_edit_recipe_in_png_round_trips_and_replaces_metadata(tmp_path: Path) -> None:
    image_path = tmp_path / "edited.png"
    Image.new("RGB", (32, 24), "white").save(image_path, format="PNG")

    assert embed_edit_recipe_in_png(image_path, {"face": {"faceWidth": -0.24}})
    assert embed_edit_recipe_in_png(
        image_path,
        {"eyes": {"eyeSize": 0.18}, "details": {"accessoryNote": "保留发饰"}},
    )

    with Image.open(image_path) as image:
        image.load()
        payload = json.loads(image.info["kigcraft.edit-recipe"])

    with Image.open(image_path) as image:
        image.verify()

    assert payload == {
        "recipe": {
            "details": {"accessoryNote": "保留发饰"},
            "eyes": {"eyeSize": 0.18},
        },
        "schema": "kigcraft.edit-recipe",
        "version": 1,
    }
    assert image_path.read_bytes().count(b"kigcraft.edit-recipe") == 2


def test_embed_edit_recipe_ignores_non_png_files(tmp_path: Path) -> None:
    image_path = tmp_path / "edited.jpg"
    Image.new("RGB", (32, 24), "white").save(image_path, format="JPEG")
    original = image_path.read_bytes()

    assert not embed_edit_recipe_in_png(image_path, {"face": {"faceWidth": -0.24}})
    assert image_path.read_bytes() == original
