from app.conversation import style_references
from app.contracts.v2 import InputSnapshot


def test_style_references_are_skipped_when_the_images_are_not_supplied(tmp_path, monkeypatch):
    monkeypatch.setattr(style_references, "STYLE_DIR", tmp_path / "missing")
    snapshot = InputSnapshot.model_construct(local_edit_mode=False, inline_local=False, target_kind="viewset")
    assert style_references.style_inputs(snapshot, "generate", tmp_path) == []
