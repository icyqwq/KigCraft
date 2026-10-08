"""Versioned, immutable inputs for separately generated alternate views."""
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator
from app.contracts.v2 import Contract, SnapshotReference
from app.design.service import digest

ViewAngle = Literal["front", "left_three_quarter", "left_profile", "right_three_quarter", "right_profile", "back"]
DEFAULT_VIEWS = ("front", "left_three_quarter", "left_profile", "back")
Feature = Annotated[StrictStr, Field(min_length=1, max_length=1000)]


MAX_EXTRA_REFERENCES = 5


class ViewSetReference(Contract):
    """An extra picture the user supplies (a side or the back, say) and what it is for."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset_id: StrictStr = Field(min_length=1, max_length=100)
    usage: StrictStr = Field(default="", max_length=1000)


class ViewSetOptions(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    views: tuple[ViewAngle, ...] = DEFAULT_VIEWS
    confirmed_features: tuple[Feature, ...] = Field(default=(), max_length=16)
    back_details: StrictStr = Field(default="", max_length=4000)
    allow_back_inference: StrictBool = False
    references: tuple[ViewSetReference, ...] = Field(default=(), max_length=MAX_EXTRA_REFERENCES)

    @model_validator(mode="after")
    def valid_views(self):
        if (not 4 <= len(self.views) <= 6 or len(set(self.views)) != len(self.views)
                or self.views[0] != "front" or self.views[-1] != "back"):
            raise ValueError("Views need one reused front, unique angles and a back")
        if not any({f"{side}_three_quarter", f"{side}_profile"} <= set(self.views) for side in ("left", "right")):
            raise ValueError("A three-quarter and profile pair is required")
        if any(not value.strip() for value in self.confirmed_features):
            raise ValueError("Confirmed features cannot be blank")
        if len({item.asset_id for item in self.references}) != len(self.references):
            raise ValueError("An extra reference can only be attached once")
        return self


class ViewSetSource(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    revision_id: StrictStr = Field(min_length=1, max_length=100)
    number: StrictInt = Field(ge=1)
    image: SnapshotReference
    width: StrictInt = Field(gt=0, le=16384)
    height: StrictInt = Field(gt=0, le=16384)

    @model_validator(mode="after")
    def base_only(self):
        if self.image.role != "base" or self.image.ordinal != 1:
            raise ValueError("The source must be the first and only base image")
        return self


class ViewSetSnapshot(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    strategy_version: Literal["separate-v1"] = "separate-v1"
    design_id: StrictStr = Field(min_length=1, max_length=100)
    branch_id: StrictStr = Field(min_length=1, max_length=100)
    branch_version: StrictInt = Field(ge=0)
    source: ViewSetSource
    options: ViewSetOptions
    # The user's extra pictures, frozen with their hashes after the source (ordinal 2 onward).
    reference_images: tuple[SnapshotReference, ...] = ()

    @model_validator(mode="after")
    def authorized_back(self):
        if not self.options.back_details.strip() and not self.options.allow_back_inference:
            raise ValueError("Unknown back details require explicit inference authorization")
        return self

    @property
    def generated_views(self) -> tuple[ViewAngle, ...]:
        return tuple(view for view in self.options.views if view != "front")

    def inferred(self, view: ViewAngle) -> bool:
        if view not in self.options.views:
            raise ValueError("View is outside the frozen selection")
        return view == "back" and not self.options.back_details.strip() and not self.reference_images

    def digest(self) -> str:
        dumped = self.model_dump(mode="json")
        # Snapshots frozen before extra references existed keep their original digest.
        if not self.reference_images:
            dumped.pop("reference_images", None)
        if not self.options.references:
            dumped["options"].pop("references", None)
        return digest(dumped)

class ViewSetRequest(Contract):
    client_request_id: StrictStr = Field(min_length=8, max_length=100)
    source_revision_id: StrictStr = Field(min_length=1, max_length=100)
    expected_branch_version: StrictInt = Field(ge=0)
    options: ViewSetOptions = Field(default_factory=ViewSetOptions)


class SubmitViewSet(ViewSetRequest):
    quote_id: StrictStr = Field(min_length=1, max_length=100)


class RetryView(Contract):
    client_request_id: StrictStr = Field(min_length=8, max_length=100)
    quote_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)

