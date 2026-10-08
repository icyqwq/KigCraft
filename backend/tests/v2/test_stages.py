"""Guided first steps: a turn can declare the kind of image it should produce."""
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.codex_runtime.session import DESIGN_INSTRUCTIONS
from app.contracts.v2 import InputSnapshot, SubmitTurn
from app.conversation.stages import FLAT_REFERENCE_BRIEF, HEAD_SHELL_BRIEF, stage_brief
from app.operations.image_kind import resolve_image_kind


def turn(**over):
    base = dict(client_request_id="request-0001", intent="generate_or_clarify", text="珊瑚色狐娘", expected_branch_version=0, quote_id="q")
    return SubmitTurn.model_validate({**base, **over})


def snapshot(**over):
    return InputSnapshot(user_text="x", base_revision_id=None, branch_version=0, references=(), **over)


def test_a_plain_first_turn_may_declare_its_target_kind():
    assert turn(target_kind="reference").target_kind == "reference"
    assert turn(target_kind="head_shell").target_kind == "head_shell"
    assert turn().target_kind is None


@pytest.mark.parametrize("extra", [
    {"answer_to": {"operation_id": "op", "version": 1}},
    {"regenerate_revision_id": "r", "text": ""},
    {"local_edit_mode": "preserve", "mask_asset_id": "m", "base_revision_id": "b"},
    {"intent": "discussion_only", "quote_id": None},
])
def test_target_kind_is_rejected_on_turns_that_keep_their_original_bindings(extra):
    with pytest.raises(ValidationError):
        turn(target_kind="reference", **extra)


def test_unknown_target_kinds_are_rejected():
    with pytest.raises(ValidationError):
        turn(target_kind="photo")


def test_snapshot_digest_is_unchanged_when_no_target_kind_is_set():
    plain = snapshot()
    assert "target_kind" not in json.dumps(plain.model_dump(mode="json", exclude={"target_kind"}))
    # The same input without the field hashes the same as before the field existed; setting it changes the digest.
    assert plain.digest() == snapshot(target_kind=None).digest()
    assert plain.digest() != snapshot(target_kind="reference").digest()


def test_briefs_follow_the_declared_kind_only():
    assert stage_brief(snapshot(target_kind="reference")) == FLAT_REFERENCE_BRIEF
    assert stage_brief(snapshot(target_kind="head_shell")) == HEAD_SHELL_BRIEF
    assert stage_brief(snapshot()) is None


def test_flat_reference_brief_states_the_requirements_and_the_exclusions():
    text = FLAT_REFERENCE_BRIEF.lower()
    for needle in ("faithfully", "hood", "hat", "mask", "accessories", "white", "front-facing", "image_kind=reference",
                   "3d", "kigurumi shell", "background", "expression"):
        assert needle in text, needle
    # Structure references are not supported, so the brief must not ask the model to look for one.
    assert "structure reference" not in text


def test_design_instructions_let_a_stage_brief_override_the_default():
    assert "stage brief" in DESIGN_INSTRUCTIONS.lower()


class FakeDb:
    def __init__(self, snapshot_value):
        self.value = snapshot_value

    async def get(self, model, key):
        return SimpleNamespace(snapshot=self.value.model_dump(mode="json"))


@pytest.mark.parametrize("chosen", ["reference", "head_shell"])
async def test_the_chosen_kind_wins_over_what_the_reply_declares(chosen):
    operation = SimpleNamespace(kind="generate", input_snapshot_id="s")
    assert await resolve_image_kind(FakeDb(snapshot(target_kind=chosen)), operation, None) == chosen


def test_every_head_shell_image_is_presented_floating_with_hair_hanging_freely():
    from app.conversation.stages import HEAD_SHELL_PRESENTATION
    text = HEAD_SHELL_PRESENTATION.lower()
    for needle in ("floating", "no stand", "table", "floor", "no neck stub", "braids", "gravity", "never rest on"):
        assert needle in text, needle
    assert HEAD_SHELL_PRESENTATION in HEAD_SHELL_BRIEF
    assert HEAD_SHELL_PRESENTATION in DESIGN_INSTRUCTIONS


def test_extra_views_use_the_same_presentation():
    from app.conversation.stages import HEAD_SHELL_PRESENTATION
    import inspect
    from app.operations import viewset
    assert "HEAD_SHELL_PRESENTATION" in inspect.getsource(viewset.generation_snapshot)


def test_head_shell_images_ask_for_a_studio_photograph_with_glossy_hair_not_an_illustration():
    from app.conversation.stages import HEAD_SHELL_LOOK, HEAD_SHELL_PRESENTATION
    text = HEAD_SHELL_LOOK.lower()
    for needle in ("studio product photograph", "85 mm", "not read as anime", "cel shading", "key light", "rim",
                   "satin sheen", "glossy", "specular", "strands", "braids", "flyaways"):
        assert needle in text, needle
    assert HEAD_SHELL_LOOK in HEAD_SHELL_PRESENTATION and HEAD_SHELL_LOOK in HEAD_SHELL_BRIEF


def test_design_turns_run_at_high_effort_on_the_luna_model():
    from app.codex_runtime.launch import DESIGN_EFFORT, DESIGN_MODEL
    assert (DESIGN_MODEL, DESIGN_EFFORT) == ("gpt-6.1-sol", "medium")
