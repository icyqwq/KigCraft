"""Extra pictures a user adds to a view set: frozen with the snapshot, sent as references, digest-compatible."""
import pytest
from pydantic import ValidationError
from app.contracts.v2 import SnapshotReference
from app.contracts.viewsets import ViewSetOptions, ViewSetSnapshot, ViewSetSource
from app.design.service import digest
from app.operations.viewset import generation_snapshot

H = "a" * 64
SOURCE = SnapshotReference(asset_id="front", sha256=H, ordinal=1, role="base", usage="front")


def snapshot(references=(), **options):
    return ViewSetSnapshot(design_id="d", branch_id="b", branch_version=1,
        source=ViewSetSource(revision_id="r", number=1, image=SOURCE, width=10, height=10),
        options=ViewSetOptions(allow_back_inference=True, references=[{"asset_id": r.asset_id, "usage": r.usage} for r in references], **options),
        reference_images=tuple(references))


def extra(asset="back-photo", usage="back of the wig"):
    return SnapshotReference(asset_id=asset, sha256=H, ordinal=2, role="reference", usage=usage)


def test_a_snapshot_without_extra_pictures_keeps_the_digest_it_had_before_they_existed():
    plain = snapshot()
    legacy = plain.model_dump(mode="json")
    legacy.pop("reference_images")
    legacy["options"].pop("references")
    assert plain.digest() == digest(legacy)


def test_extra_pictures_change_the_digest_and_are_sent_after_the_front():
    plain, with_back = snapshot(), snapshot([extra()])
    assert plain.digest() != with_back.digest()
    sent = generation_snapshot(with_back, "back", expected_digest=with_back.digest())
    assert [item.asset_id for item in sent.references] == ["front", "back-photo"]
    assert "back of the wig" in sent.user_text and "extra references" in sent.user_text
    assert len(generation_snapshot(plain, "back", expected_digest=plain.digest()).references) == 1


def test_a_back_picture_means_the_rear_is_not_inferred():
    assert snapshot().inferred("back") is True
    assert snapshot([extra()]).inferred("back") is False


def test_the_same_picture_cannot_be_attached_twice_or_past_the_limit():
    with pytest.raises(ValidationError):
        ViewSetOptions(references=[{"asset_id": "x", "usage": ""}, {"asset_id": "x", "usage": ""}])
    with pytest.raises(ValidationError):
        ViewSetOptions(references=[{"asset_id": str(i), "usage": ""} for i in range(6)])
