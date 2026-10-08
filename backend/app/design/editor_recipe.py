"""Validation for the current Pixi engine, without normalizing or rewriting recipes.

Keep aligned with frontend/features/editor/core/recipeSchema.ts and the shared
v2-editor-document fixture. Legacy partial PNG recipes use their separate importer.
"""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Finite = Annotated[float, Field(allow_inf_nan=False)]
Unit = Annotated[Finite, Field(ge=0, le=1)]
Identity = Annotated[str, Field(min_length=1, max_length=100)]
Text = Annotated[str, Field(max_length=10000)]

class RecipePart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, value):
        # Optional fields may be absent, but null is not a supported engine value.
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("Recipe fields cannot be null")
        return value

class RecipeCoordinates(RecipePart):
    version: StrictInt = Field(ge=1, le=1)
    width: StrictInt = Field(gt=0, le=16384)
    height: StrictInt = Field(gt=0, le=16384)
    effectScale: Finite = Field(gt=0, le=16384)

class FaceControls(RecipePart):
    faceWidth: Finite = Field(ge=-.4, le=.4)
    faceLength: Finite = Field(ge=-.6, le=.6)
    midFaceLength: Finite = Field(ge=-.4, le=.4)
    smallFace: Finite = Field(ge=-.4, le=.4)
    cheekbone: Finite = Field(ge=-.4, le=.4)
    chinLength: Finite = Field(ge=-.4, le=.4)
    chinPoint: Finite = Field(ge=-.4, le=.4)
    vLine: Finite = Field(ge=0, le=.4)
    jawAngle: Finite = Field(ge=-.4, le=.4)
    temple: Finite = Field(ge=-.4, le=.4)

class EyeControls(RecipePart):
    eyeSize: Finite = Field(ge=-.6, le=.6)
    eyeHeight: Finite = Field(ge=-.6, le=.6)
    eyeWidth: Finite = Field(ge=-1, le=1)
    eyeDistance: Finite = Field(ge=-.1, le=.1)
    eyeVertical: Finite = Field(ge=-.1, le=.1)
    eyeTilt: Finite = Field(ge=-1, le=1)
    eyeUpperLid: Finite = Field(ge=-1, le=1)
    eyeIrisSize: Finite = Field(ge=-1, le=1)
    eyeLowerLid: Finite = Field(ge=-1, le=1)
    eyeTailLift: Finite = Field(ge=-1, le=1)
    eyeRegionScale: Finite = Field(ge=-80, le=120)

class BrowControls(RecipePart):
    browVertical: Finite = Field(ge=-1, le=1)
    browThickness: Finite = Field(ge=-1, le=1)
    browLength: Finite = Field(ge=-1, le=1)
    browSpacing: Finite = Field(ge=-1, le=1)
    browHeadSpacing: Finite = Field(ge=-1, le=1)
    browTilt: Finite = Field(ge=-1, le=1)
    browPeak: Finite = Field(ge=-1, le=1)

class MouthControls(RecipePart):
    mouthHorizontal: Finite = Field(ge=-.05, le=.05)
    mouthVertical: Finite = Field(ge=-.06, le=.06)
    mouthWidth: Finite = Field(ge=-.45, le=.45)
    mouthSize: Finite = Field(ge=-.35, le=.35)
    mouthSmile: Finite = Field(ge=-.08, le=.08)

class LiquifyStroke(RecipePart):
    x: Unit
    y: Unit
    radius: Finite = Field(gt=0, le=16384 * 240)
    strength: Unit
    mode: Literal["expand", "shrink", "push-left", "push-right", "push-up", "push-down", "warp", "scale"]
    deltaX: Finite | None = Field(default=None, ge=-.35, le=.35)
    deltaY: Finite | None = Field(default=None, ge=-.35, le=.35)
    scale: Finite | None = Field(default=None, ge=-1, le=1)

class AnnotationGeometry(RecipePart):
    id: Identity
    kind: Literal["callout", "box", "pin", "arrow", "rect", "text"]
    x: Unit
    y: Unit
    width: Unit
    height: Unit
    endX: Unit
    endY: Unit
    note: Text
    color: str | None = Field(default=None, pattern=r"^#[a-fA-F0-9]{3,8}$")
    fontSize: Finite | None = Field(default=None, ge=1, le=256)
    strokeWidth: Finite | None = Field(default=None, gt=0, le=256)
    text: Text | None = None

class DetailRegion(RecipePart):
    id: Identity
    x: Unit
    y: Unit
    radius: Finite = Field(gt=0, le=1)

class Details(RecipePart):
    preserveSkinTexture: bool
    preserveHairEdges: bool
    protectBangs: bool
    protectAccessories: bool
    manualLandmarkEdit: bool
    calibrationMode: bool
    accessoryNote: Text
    refineIntensity: Unit
    regions: list[DetailRegion] = Field(max_length=1000)

class LandmarkPoint(RecipePart):
    # Detection can put points outside the image. Preserve rather than clamp.
    x: Finite = Field(ge=-4, le=4)
    y: Finite = Field(ge=-4, le=4)

class EyeRegion(RecipePart):
    radiusX: Finite = Field(gt=0, le=4)
    radiusY: Finite = Field(gt=0, le=4)
    radiusTopY: Finite = Field(gt=0, le=4)
    radiusBottomY: Finite = Field(gt=0, le=4)

class EyeRegions(RecipePart):
    left: EyeRegion
    right: EyeRegion

class BrowLandmark(RecipePart):
    inner: LandmarkPoint
    peak: LandmarkPoint
    outer: LandmarkPoint

class BrowLandmarks(RecipePart):
    left: BrowLandmark
    right: BrowLandmark

class Landmarks(RecipePart):
    leftEye: LandmarkPoint
    rightEye: LandmarkPoint
    chin: LandmarkPoint
    jawLeft: LandmarkPoint
    jawRight: LandmarkPoint
    mouthCenter: LandmarkPoint
    mouthLeft: LandmarkPoint
    mouthRight: LandmarkPoint
    eyeRegions: EyeRegions | None = None
    brows: BrowLandmarks | None = None

class NormalizedRecipe(RecipePart):
    imageCoordinates: RecipeCoordinates
    face: FaceControls
    eyes: EyeControls
    brows: BrowControls
    mouth: MouthControls
    liquify: list[LiquifyStroke] = Field(max_length=10000)
    annotations: list[AnnotationGeometry] = Field(max_length=1000)
    details: Details
    landmarks: Landmarks | None = None
