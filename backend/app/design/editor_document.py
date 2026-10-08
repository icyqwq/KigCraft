"""Versioned editor state. Annotation geometry occurs only in normalizedRecipe."""
from __future__ import annotations
import copy
import json
from typing import Literal
from pydantic import Field, StrictInt, field_validator, model_validator
from app.design.drafts import StrictModel, MaskStroke, MaskBinding
from app.design.manual import ImageCoordinates
from app.design.editor_recipe import NormalizedRecipe

class EditorRoot(StrictModel):
    assetId: str = Field(min_length=1, max_length=36)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: StrictInt = Field(gt=0, le=16384)
    height: StrictInt = Field(gt=0, le=16384)

class AnnotationIntent(StrictModel):
    annotationId: str = Field(min_length=1, max_length=100)
    intent: Literal["instruction", "preserve", "modify"]
    text: str = Field(max_length=10000)

class MaskDocument(StrictModel):
    coordinateSpace: Literal["normalized-image"]
    radiusUnit: Literal["image-pixel"]
    strokes: list[MaskStroke] = Field(max_length=10000)
    binding: MaskBinding | None = None

class InitialEditorDocument(StrictModel):
    schemaVersion: StrictInt = Field(ge=1, le=1)
    engineVersion: Literal["kigcraft-pixi-1"]
    root: EditorRoot
    normalizedRecipe: dict
    calibrationRevision: str = Field(min_length=1, max_length=100)
    annotationIntents: list[AnnotationIntent] = Field(max_length=1000)
    maskDocument: MaskDocument

    @field_validator("normalizedRecipe")
    @classmethod
    def supported_recipe(cls, value):
        NormalizedRecipe.model_validate(value)
        # Preserve exact JSON numbers, absent optionals and key order for receipts/bindings.
        return value

    @model_validator(mode="after")
    def coherent(self):
        if len(json.dumps(self.model_dump(mode="json"), allow_nan=False).encode()) > 4 * 1024 * 1024:
            raise ValueError("Editor document too large")
        coords = ImageCoordinates.model_validate(self.normalizedRecipe.get("imageCoordinates"))
        if (coords.width, coords.height) != (self.root.width, self.root.height):
            raise ValueError("Recipe size mismatch")
        marks = self.normalizedRecipe.get("annotations", [])
        if not isinstance(marks, list) or len(marks) > 1000:
            raise ValueError("Invalid annotations")
        ids = []
        for mark in marks:
            if not isinstance(mark, dict) or not isinstance(mark.get("id"), str) or not 0 < len(mark["id"]) <= 100:
                raise ValueError("Invalid annotation identity")
            if mark.get("note", ""):
                raise ValueError("Annotation instructions belong in annotationIntents")
            ids.append(mark["id"])
        intents = [item.annotationId for item in self.annotationIntents]
        if len(set(ids)) != len(ids) or len(set(intents)) != len(intents) or set(ids) != set(intents):
            raise ValueError("Annotation identities must match exactly")
        binding = self.maskDocument.binding
        if binding and (binding.sourceKey, binding.width, binding.height) != (self.root.assetId, self.root.width, self.root.height):
            raise ValueError("Mask source mismatch")
        if self.maskDocument.strokes and not binding:
            raise ValueError("Mask strokes require their original geometry binding")
        if len({stroke.id for stroke in self.maskDocument.strokes}) != len(self.maskDocument.strokes):
            raise ValueError("Duplicate mask stroke")
        return self

    def legacy_recipe(self):
        # Transitional consumers render geometry and free-form notes from the old recipe shape.
        recipe = copy.deepcopy(self.normalizedRecipe)
        instructions = {item.annotationId: item.text for item in self.annotationIntents}
        for mark in recipe.get("annotations", []):
            mark["note"] = instructions[mark["id"]]
        return recipe


class EditorDocument(InitialEditorDocument):
    baseRevision: str = Field(min_length=1, max_length=36)
