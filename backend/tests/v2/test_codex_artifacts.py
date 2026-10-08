import base64
import io
from pathlib import Path
import pytest
from PIL import Image
from app.codex_runtime.artifacts import ArtifactError, stage_image

def image_bytes(color="coral", size=(16, 16)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()

def collect(tmp_path, **changes):
    item = {"id": "image-1", "type": "imageGeneration", "status": "completed",
            "result": base64.b64encode(image_bytes()).decode(), **changes}
    return stage_image(item, runtime_id="runtime", thread_id="thread", turn_id="turn",
                       output_root=tmp_path, staging_root=tmp_path / "staging")

def test_replay_is_same_private_immutable_delivery(tmp_path):
    first, second = collect(tmp_path), collect(tmp_path)
    assert first == second
    assert list((tmp_path / "staging").iterdir()) == [first.path]
    with Image.open(first.path) as image:
        assert image.format == "JPEG"
        assert image.size == (16, 16)

def test_duplicate_identity_cannot_replace_image(tmp_path):
    original = collect(tmp_path)
    with pytest.raises(ArtifactError, match="identity_conflict"):
        collect(tmp_path, result=base64.b64encode(image_bytes("blue")).decode())
    assert collect(tmp_path).sha256 == original.sha256

@pytest.mark.parametrize("changes", [
    {"type": "imageView"}, {"status": "inProgress"}, {"failure": {"message": "error"}},
    {"result": "not an image"}, {"result": base64.b64encode(b"broken").decode()},
    {"result": "data:text/plain;base64,YWJj"}, {"id": ""},
])
def test_invalid_or_non_generation_results_rejected(tmp_path, changes):
    with pytest.raises(ArtifactError):
        collect(tmp_path, **changes)
    assert not (tmp_path / "staging").exists()

def test_saved_path_must_be_operation_local(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(image_bytes())
    operation = tmp_path / "operation"
    operation.mkdir()
    with pytest.raises(ArtifactError, match="outside_operation"):
        collect(operation, result="", savedPath=str(outside))
    inside = operation / "image.png"
    inside.write_bytes(image_bytes())
    assert collect(operation, result="", savedPath=str(inside)).width == 16

def test_path_traversal_rejected(tmp_path):
    with pytest.raises(ArtifactError, match="outside_operation"):
        collect(tmp_path, result="", savedPath="../outside.png")

def test_pixel_limit_checked_before_decode(tmp_path):
    item = {"id": "large", "type": "imageGeneration", "status": "completed",
            "result": base64.b64encode(image_bytes(size=(101, 101))).decode()}
    with pytest.raises(ArtifactError, match="dimensions_exceeded"):
        stage_image(item, runtime_id="r", thread_id="t", turn_id="u", output_root=tmp_path,
                    staging_root=tmp_path / "staging", max_pixels=10000)
