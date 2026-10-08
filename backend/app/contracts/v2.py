from __future__ import annotations
import copy
import hashlib
import json
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperationKind(StrEnum):
    generate = "generate"
    image_edit = "image_edit"
    local_edit = "local_edit"
    regenerate = "regenerate"
    viewset = "viewset"
    discussion = "discussion"


class OperationState(StrEnum):
    queued = "queued"
    reviewing = "reviewing"
    awaiting_input = "awaiting_input"
    running = "running"
    reconciling = "reconciling"
    publishing = "publishing"
    cancelling = "cancelling"
    succeeded = "succeeded"
    partial_success = "partial_success"
    failed = "failed"
    cancelled = "cancelled"


class ReferenceAttachment(Contract):
    asset_id: StrictStr = Field(min_length=1, max_length=100)
    usage: StrictStr = Field(max_length=2000)


class ContinuationDraft(Contract):
    source_revision_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    boundary_message_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def boundary_required(self):
        if not self.source_revision_id and not self.boundary_message_id:
            raise ValueError("A revision or message boundary is required")
        return self



class PreparedEdit(Contract):
    schema_version: StrictInt = Field(default=1, ge=1, le=1)
    clean_asset_id: StrictStr = Field(min_length=1, max_length=100)
    root_source_asset_id: StrictStr = Field(min_length=1, max_length=100)
    root_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    width: StrictInt = Field(gt=0, le=16384)
    height: StrictInt = Field(gt=0, le=16384)
    recipe: dict

    @model_validator(mode="after")
    def bounded_recipe(self):
        try:
            if len(json.dumps(self.recipe, allow_nan=False).encode()) > 1024 * 1024:
                raise ValueError("Recipe too large")
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Invalid recipe") from None
        marks = self.recipe.get("annotations", [])
        if not isinstance(marks, list) or len(marks) > 1000:
            raise ValueError("Invalid annotation bindings")
        identities = []
        for mark in marks:
            if not isinstance(mark, dict) or not isinstance(mark.get("id"), str) or not 0 < len(mark["id"]) <= 100:
                raise ValueError("Invalid annotation identity")
            if any(not isinstance(mark.get(key, ""), str) or len(mark.get(key, "")) > 10000 for key in ("note", "text")):
                raise ValueError("Invalid annotation text")
            identities.append(mark["id"])
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate annotation identity")
        return self

class AnswerBinding(Contract):
    operation_id: StrictStr = Field(min_length=1, max_length=100)
    version: StrictInt = Field(ge=1)


class SubmitTurn(Contract):
    answer_to: AnswerBinding | None = None
    schema_version: Literal[1] = 1
    client_request_id: StrictStr = Field(min_length=8, max_length=100)
    intent: Literal["generate_or_clarify", "discussion_only"]
    text: StrictStr = Field(max_length=16000)
    base_revision_id: StrictStr | None = Field(default=None, max_length=100)
    expected_branch_version: StrictInt = Field(ge=0)
    continuation: ContinuationDraft | None = None
    regenerate_revision_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    attachments: list[ReferenceAttachment] = Field(default_factory=list)
    annotation_asset_id: StrictStr | None = Field(default=None, max_length=100)
    mask_asset_id: StrictStr | None = Field(default=None, max_length=100)
    local_edit_mode: Literal["preserve", "lock_outside"] | None = None
    prepared_edit: PreparedEdit | None = None
    quote_id: StrictStr | None = Field(default=None, max_length=100)
    # The editor's own local generation: it runs in a throw-away session, writes no message and creates no version.
    inline_local: StrictBool = False
    # What this turn should produce: a clean flat reference image, or the realistic head-shell render.
    # Unset leaves the choice to the conversation (follow-up edits keep the base image kind).
    target_kind: Literal["reference", "head_shell", "four_view"] | None = None

    @model_validator(mode="after")
    def valid_intent(self) -> SubmitTurn:
        if self.answer_to and (self.continuation or self.regenerate_revision_id or self.local_edit_mode
                or self.prepared_edit or self.annotation_asset_id or self.mask_asset_id or self.intent != "generate_or_clarify"):
            raise ValueError("Answers retain the question's original input bindings")
        if not self.text.strip() and not self.attachments and not self.regenerate_revision_id:
            raise ValueError("Text or a reference is required")
        if self.intent != "discussion_only" and not self.quote_id:
            raise ValueError("Generation requires a quote")
        if self.mask_asset_id and not self.base_revision_id:
            raise ValueError("A local mask requires its base revision")
        if self.local_edit_mode and (not self.mask_asset_id or self.intent == "discussion_only"):
            raise ValueError("Local edit mode requires an image edit mask")
        if self.prepared_edit and (not self.base_revision_id or not self.mask_asset_id):
            raise ValueError("Prepared edit requires its base and mask")
        if self.inline_local and not (self.local_edit_mode and self.prepared_edit and self.mask_asset_id):
            raise ValueError("Inline local generation needs the editor's prepared edit and mask")
        if self.regenerate_revision_id and (self.local_edit_mode or self.prepared_edit or self.text or self.attachments or self.base_revision_id
                or self.continuation or self.annotation_asset_id or self.mask_asset_id or self.intent == "discussion_only"):
            raise ValueError("Regeneration uses only the original immutable request")
        if self.continuation and self.continuation.boundary_message_id and (self.local_edit_mode or self.prepared_edit or self.annotation_asset_id or self.mask_asset_id):
            raise ValueError("Message editing retains original annotation and local edit bindings")
        if self.target_kind and (self.answer_to or self.regenerate_revision_id or self.local_edit_mode or self.prepared_edit
                or self.mask_asset_id or self.annotation_asset_id or self.intent == "discussion_only"):
            raise ValueError("A target kind belongs to a plain generation turn")
        ids = [a.asset_id for a in self.attachments]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate reference identity")
        return self


class QuoteRequest(SubmitTurn):
    quote_id: Literal[None] = None

    @model_validator(mode="after")
    def valid_intent(self) -> QuoteRequest:
        # Validate the same input, without requiring a quote that is being created.
        SubmitTurn.model_validate({**self.model_dump(), "quote_id": "quote-preview"})
        return self


class SnapshotReference(ReferenceAttachment):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    ordinal: StrictInt = Field(ge=1)
    role: Literal["base", "reference", "annotation", "mask"]


class InputSnapshot(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    user_text: StrictStr
    base_revision_id: StrictStr | None
    branch_version: StrictInt = Field(ge=0)
    references: tuple[SnapshotReference, ...]
    annotation_notes: tuple[StrictStr, ...] = ()
    local_edit_mode: Literal["preserve", "lock_outside"] | None = None
    prepared_edit: PreparedEdit | None = None
    target_kind: Literal["reference", "head_shell", "four_view"] | None = None
    inline_local: StrictBool = False
    style_version: StrictStr = "physical-shell-v1"
    runtime_config_version: StrictStr = "codex-0.155-v1"

    def working_context(self) -> dict:
        from app.conversation.bindings import annotation_bindings
        context = self.model_dump(mode="json", exclude={"user_text"})
        number = 0
        for reference in context["references"]:
            if reference["role"] == "reference":
                number += 1
                reference["reference_number"] = number
        context["image_numbering"] = {"ordinal": "position among all image inputs",
            "reference_number": "message-local user reference number; base, annotation and mask images do not count"}
        context["annotation_bindings"] = annotation_bindings(self)
        if self.prepared_edit:
            # Geometry is already baked into the clean master; retain it in history, not in model instructions.
            recipe = context["prepared_edit"].pop("recipe")
            context["prepared_edit"]["annotations"] = recipe.get("annotations", [])
        return context

    def annotation_review(self) -> list[dict]:
        annotations = copy.deepcopy(self.prepared_edit.recipe.get("annotations", [])) if self.prepared_edit else []
        from app.conversation.bindings import annotation_bindings
        return [{"role": "annotation_instructions", "annotations": annotations, "numbered_annotations": annotation_bindings(self), "notes": list(self.annotation_notes)}] if annotations or self.annotation_notes else []

    def digest(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json", exclude={name for name in ("local_edit_mode", "prepared_edit", "target_kind", "inline_local") if getattr(self, name) in (None, False)}), sort_keys=True,
                               ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class V2Capabilities(Contract):
    schema_version: Literal[1] = 1
    enabled: bool = False
    new_operations: bool = False
    discussion: bool = False
    generation: bool = False
    history_fork: bool = False
    viewsets: bool = False
    local_edits: bool = False
    max_input_images: int = Field(default=8, ge=1)
    # Conversations a regular account may keep (archived ones excluded); None = no limit.
    design_limit: int | None = Field(default=None, ge=1)
    reason: str | None = None
