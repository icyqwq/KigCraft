import hashlib
import pytest
from app.contracts.v2 import SubmitTurn
from app.conversation.context import AuthorizedImage, ReferenceLimitExceeded, build_context

def request(**extra):
    return SubmitTurn.model_validate({"client_request_id": "request-1", "intent": "generate_or_clarify",
        "text": "  smile, preserve the wig\n", "expected_branch_version": 1, "quote_id": "quote-1", **extra})

def image(tmp_path, identity="ref", role="reference", usage="wig fibers only"):
    path = tmp_path / (identity + ".png")
    path.write_bytes(b"authorized fixture bytes")
    return AuthorizedImage(identity, hashlib.sha256(path.read_bytes()).hexdigest(), path, role, usage)

def build(payload, images, **limits):
    return build_context(payload, images, max_input_images=limits.get("count", 10),
                         max_total_bytes=limits.get("size", 1000))

def test_preserves_user_text_usage_order_and_private_paths(tmp_path):
    ref = image(tmp_path)
    payload = request(attachments=[{"asset_id": "ref", "usage": ref.usage}])
    snapshot, inputs = build(payload, [ref])
    assert snapshot.user_text == payload.text
    assert inputs[0]["text"] == payload.text
    assert snapshot.references[0].ordinal == 1
    assert str(tmp_path) not in snapshot.model_dump_json()
    assert inputs[2]["path"] == str(ref.path.resolve())

@pytest.mark.parametrize("limit", [{"count": 1}, {"size": 1}])
def test_limits_never_silently_drop_references(tmp_path, limit):
    images = [image(tmp_path, "a"), image(tmp_path, "b")]
    payload = request(attachments=[{"asset_id": i.asset_id, "usage": i.usage} for i in images])
    with pytest.raises(ReferenceLimitExceeded) as error:
        build(payload, images, **limit)
    assert error.value.asset_ids == ["a", "b"]

def test_missing_selected_reference_rejected(tmp_path):
    with pytest.raises(ValueError, match="match all selected"):
        build(request(attachments=[{"asset_id": "missing", "usage": "color"}]), [])

def test_resolved_images_cannot_reverse_user_reference_numbers(tmp_path):
    images = [image(tmp_path, "a"), image(tmp_path, "b")]
    payload = request(attachments=[{"asset_id": item.asset_id, "usage": item.usage} for item in images])
    with pytest.raises(ValueError, match="in order"):
        build(payload, list(reversed(images)))


def test_changed_input_cannot_be_submitted(tmp_path):
    ref = image(tmp_path)
    ref.path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        build(request(attachments=[{"asset_id": "ref", "usage": ref.usage}]), [ref])

def test_base_and_mask_bindings_are_explicit(tmp_path):
    with pytest.raises(ValueError, match="Base image"):
        build(request(base_revision_id="revision-1"), [])
