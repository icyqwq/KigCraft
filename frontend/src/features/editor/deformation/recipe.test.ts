import { describe, expect, it } from "vitest";
import {
  addAnnotationMark,
  addDetailRegion,
  buildAnnotationPrompt,
  compactRecipeAnnotations,
  createEmptyRecipe,
  createLiquifyStrokeFromNormalizedPoint,
  createLiquifyWarpStrokeFromDrag,
  moveAnnotationMark,
  normalizeEditRecipe,
  removeAnnotationMark,
  updateLiquifyScaleBrush,
  updateAnnotationNote,
  updateDetailSetting,
  updateEyeControl,
  updateFaceControl,
  updateLiquifyBrush,
  updateManualLandmark,
  updateMouthControl,
} from "./recipe";

describe("editor recipe", () => {
  it("stores annotation marks created from canvas clicks", () => {
    const recipe = addAnnotationMark(createEmptyRecipe(), {
      note: "keep hair edge",
      x: 0.72,
      y: 0.36,
    });

    expect(recipe.annotations).toEqual([
      expect.objectContaining({
        id: "annotation-1",
        kind: "callout",
        note: "keep hair edge",
        x: 0.72,
        y: 0.36,
      }),
    ]);
  });

  it("updates annotation notes by mark id", () => {
    const recipe = updateAnnotationNote(
      addAnnotationMark(createEmptyRecipe(), {
        x: 0.4,
        y: 0.5,
      }),
      "annotation-1",
      "keep eyelash angle",
    );

    expect(recipe.annotations[0]).toEqual(expect.objectContaining({
      id: "annotation-1",
      note: "keep eyelash angle",
    }));
  });

  it("moves annotation pins by id and keeps them inside image bounds", () => {
    const recipe = moveAnnotationMark(
      addAnnotationMark(createEmptyRecipe(), {
        x: 0.4,
        y: 0.5,
      }),
      "annotation-1",
      { x: 1.4, y: -0.2 },
    );

    expect(recipe.annotations[0]).toEqual(expect.objectContaining({
      id: "annotation-1",
      x: 1,
      y: 0,
    }));
  });

  it("removes annotation pins by id", () => {
    const recipeWithMarks = addAnnotationMark(
      addAnnotationMark(createEmptyRecipe(), {
        x: 0.2,
        y: 0.3,
      }),
      {
        x: 0.6,
        y: 0.7,
      },
    );

    const recipe = removeAnnotationMark(recipeWithMarks, "annotation-1");

    expect(recipe.annotations).toEqual([
      expect.objectContaining({
        id: "annotation-2",
        x: 0.6,
        y: 0.7,
      }),
    ]);
  });

  it("drops empty annotation pins before save or regeneration", () => {
    const recipe = updateAnnotationNote(
      addAnnotationMark(
        addAnnotationMark(createEmptyRecipe(), {
          x: 0.2,
          y: 0.3,
        }),
        {
          x: 0.6,
          y: 0.7,
        },
      ),
      "annotation-2",
      "keep right eye highlight",
    );

    const compacted = compactRecipeAnnotations(recipe);

    expect(compacted.annotations).toEqual([
      expect.objectContaining({
        id: "annotation-1",
        note: "keep right eye highlight",
        x: 0.6,
        y: 0.7,
      }),
    ]);
  });

  it("combines annotation content into a generation prompt segment", () => {
    const recipe = compactRecipeAnnotations(
      updateAnnotationNote(
        addAnnotationMark(createEmptyRecipe(), {
          x: 0.25,
          y: 0.5,
        }),
        "annotation-1",
        "preserve the left eyelash curve",
      ),
    );

    expect(buildAnnotationPrompt(recipe.annotations)).toBe(
      "鏍囨敞 1锛?5%, 50%锛夛細preserve the left eyelash curve",
    );
  });

  it("clamps manual liquify strokes to the image bounds", () => {
    const stroke = createLiquifyStrokeFromNormalizedPoint({
      mode: "push-left",
      radius: 80,
      strength: 0.6,
      x: 2,
      y: -1,
    });

    expect(stroke).toEqual({
      mode: "push-left",
      radius: 80,
      strength: 0.6,
      x: 1,
      y: 0,
    });
  });

  it("stores deformation brush drag direction and distance in the liquify recipe", () => {
    const stroke = createLiquifyWarpStrokeFromDrag({
      from: { x: 0.2, y: 0.4 },
      radius: 72,
      to: { x: 0.31, y: 0.35 },
    });

    expect(stroke).toEqual({
      deltaX: 0.11,
      deltaY: -0.05,
      mode: "warp",
      radius: 72,
      strength: 1,
      x: 0.255,
      y: 0.375,
    });
  });

  it("keeps local scale as a single adjustable cursor in the liquify recipe", () => {
    const recipe = updateLiquifyScaleBrush(
      updateLiquifyScaleBrush(createEmptyRecipe(), {
        radius: 80,
        scale: 0.35,
        x: 0.25,
        y: 0.4,
      }),
      {
        radius: 112,
        scale: -0.45,
        x: 0.6,
        y: 0.45,
      },
    );

    expect(recipe.liquify).toEqual([
      {
        mode: "scale",
        radius: 112,
        scale: -0.45,
        strength: 0.45,
        x: 0.6,
        y: 0.45,
      },
    ]);
  });

  it("updates manual landmark positions and keeps them inside image bounds", () => {
    const recipe = updateManualLandmark(createEmptyRecipe(), "leftEye", { x: 1.2, y: -0.1 });

    expect(recipe.landmarks?.leftEye).toEqual({ x: 1, y: 0 });
    expect(recipe.landmarks?.rightEye).toEqual({ x: 0.58, y: 0.42 });
    expect(recipe.landmarks?.mouthCenter).toEqual({ x: 0.5, y: 0.57 });
  });

  it("clamps mouth controls to their real algorithm ranges", () => {
    const recipe = updateMouthControl(
      updateMouthControl(
        updateMouthControl(
          updateMouthControl(updateMouthControl(createEmptyRecipe(), "mouthHorizontal", 1), "mouthVertical", -1),
          "mouthWidth",
          1,
        ),
        "mouthSize",
        -1,
      ),
      "mouthSmile",
      1,
    );

    expect(recipe.mouth.mouthHorizontal).toBe(0.05);
    expect(recipe.mouth.mouthVertical).toBe(-0.06);
    expect(recipe.mouth.mouthWidth).toBe(0.45);
    expect(recipe.mouth.mouthSize).toBe(-0.35);
    expect(recipe.mouth.mouthSmile).toBe(0.08);
  });

  it("stores detail settings and detail regions for local refinements", () => {
    const recipe = addDetailRegion(
      updateDetailSetting(createEmptyRecipe(), "accessoryNote", "preserve green hair streaks"),
      {
        radius: 0.12,
        x: 0.28,
        y: 0.64,
      },
    );

    expect(recipe.details.accessoryNote).toBe("preserve green hair streaks");
    expect(recipe.details.regions).toEqual([
      expect.objectContaining({
        id: "detail-1",
        radius: 0.12,
        x: 0.28,
        y: 0.64,
      }),
    ]);
  });

  it("keeps face, eye, mouth, and manual liquify edits in one recipe payload", () => {
    const recipe = updateLiquifyBrush(
      updateMouthControl(
        updateEyeControl(updateFaceControl(createEmptyRecipe(), "vLine", 2.4), "eyeSize", 1.8),
        "mouthSmile",
        0.08,
      ),
      createLiquifyStrokeFromNormalizedPoint({
        mode: "expand",
        radius: 72,
        strength: 0.48,
        x: 0.45,
        y: 0.4,
      }),
    );

    expect(recipe.face.vLine).toBe(0.4);
    expect(recipe.eyes.eyeSize).toBe(0.6);
    expect(recipe.mouth.mouthSmile).toBe(0.08);
    expect(recipe.liquify).toHaveLength(1);
  });

  it("clamps face and eye controls to the real algorithm ranges", () => {
    const faceRecipe = updateFaceControl(
      updateFaceControl(
        updateFaceControl(
          updateFaceControl(
            updateFaceControl(createEmptyRecipe(), "faceWidth", 1),
            "faceLength",
            -1,
          ),
          "smallFace",
          1,
        ),
        "midFaceLength",
        1,
      ),
      "jawAngle",
      -1,
    );
    const finalRecipe = updateEyeControl(
      updateEyeControl(updateEyeControl(faceRecipe, "eyeSize", 1), "eyeDistance", -1),
      "eyeTilt",
      1,
    );

    expect(finalRecipe.face.faceWidth).toBe(0.4);
    expect(finalRecipe.face.faceLength).toBe(-0.6);
    expect(finalRecipe.face.midFaceLength).toBe(0.4);
    expect(finalRecipe.face.smallFace).toBe(0.4);
    expect(finalRecipe.face.jawAngle).toBe(-0.4);
    expect(finalRecipe.eyes.eyeSize).toBe(0.6);
    expect(finalRecipe.eyes.eyeDistance).toBe(-0.1);
    expect(finalRecipe.eyes.eyeTilt).toBe(1);
  });

  it("maps legacy eyeHeight recipes to the renamed eyeSize control", () => {
    const legacyRecipe = {
      eyes: {
        eyeDistance: 0,
        eyeHeight: 7.5,
        eyeTilt: 0,
        eyeVertical: 0,
        eyeWidth: 0,
      },
    } as Parameters<typeof normalizeEditRecipe>[0];
    const recipe = normalizeEditRecipe(legacyRecipe);

    expect(recipe.eyes.eyeSize).toBe(7.5);
    expect(recipe.eyes.eyeHeight).toBe(0);
  });
});

