"""A four-view sheet is one ordinary generation turn whose brief asks for a single image with four directions."""
from types import SimpleNamespace
from app.contracts.v2 import InputSnapshot, SubmitTurn
from app.conversation.stages import FOUR_VIEW_BRIEF, stage_brief


def test_the_turn_kind_is_accepted_and_selects_the_sheet_brief():
    turn = SubmitTurn(schema_version=1, client_request_id="request-1", intent="generate_or_clarify", text="x",
                      base_revision_id="r", expected_branch_version=0, quote_id="q", target_kind="four_view")
    assert turn.target_kind == "four_view"
    assert stage_brief(SimpleNamespace(target_kind="four_view")) == FOUR_VIEW_BRIEF
    assert InputSnapshot(user_text="x", base_revision_id="r", branch_version=0, references=(), target_kind="four_view").target_kind == "four_view"


def test_the_brief_asks_for_one_image_with_four_directions_and_no_separate_pictures():
    text = FOUR_VIEW_BRIEF
    assert "exactly ONE image" in text and "do not output four separate images" in text
    for direction in ("front", "three-quarter", "side profile", "back"):
        assert direction in text
    assert "image_kind=head_shell" in text
