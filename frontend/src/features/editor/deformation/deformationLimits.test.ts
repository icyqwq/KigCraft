import { describe, expect, it } from "vitest";
import { animeFaceFeatures, animeFaceLandmarks, animeFaceSize } from "./fixtures/animeFace";
import { calculateRecipePreview, createMeshDeformer } from "./pixiStage";
import {
  browControlRanges,
  createEmptyRecipe,
  eyeControlRanges,
  faceControlRanges,
  mouthControlRanges,
  proportionControlKeys,
  updateBrowControl,
  updateEyeControl,
  updateFaceControl,
  updateMouthControl,
  type EditRecipe,
  type FaceControlKey,
} from "./recipe";

type Section = "face" | "eyes" | "brows" | "mouth";

const effectScale = Math.max(animeFaceSize.width, animeFaceSize.height) / 720;

function recipeWith(section: Section, key: string, value: number): EditRecipe {
  const recipe = createEmptyRecipe();
  if (section === "face") return updateFaceControl(recipe, key as FaceControlKey, value);
  if (section === "eyes") return updateEyeControl(recipe, key as never, value);
  if (section === "brows") return updateBrowControl(recipe, key as never, value);
  return updateMouthControl(recipe, key as never, value);
}

/** Image-pixel offset function for one recipe on the anime portrait, as the anchored renderer computes it. */
function deformerFor(recipe: EditRecipe) {
  const preview = calculateRecipePreview(recipe, { landmarks: animeFaceLandmarks });
  const toImagePixels = (strokes: typeof preview.featureLiquifyStrokes) =>
    strokes.map((stroke) => ({ ...stroke, radius: stroke.radius * effectScale }));
  const deform = createMeshDeformer({
    textureWidth: animeFaceSize.width,
    textureHeight: animeFaceSize.height,
    fitScale: 1,
    pixelRatio: effectScale,
    strokeRadiiInImagePixels: true,
    faceShape: preview.faceShapeTransform,
    strokes: toImagePixels(preview.featureLiquifyStrokes),
    protectionZones: preview.featureProtectionZones,
    eyeTransforms: preview.eyeMeshTransforms,
    browTransforms: preview.browMeshTransforms,
    mouthTransforms: preview.mouthMeshTransforms,
  });
  return (x: number, y: number) => deform?.(x, y) ?? { x: 0, y: 0 };
}

/** Smallest local area ratio over the face; 1 is untouched, 0 or below folds the image over. */
function minimumAreaRatio(recipe: EditRecipe) {
  const deform = deformerFor(recipe);
  let minimum = Infinity;
  for (let y = 380; y < 1000; y += 6) {
    for (let x = 120; x < 780; x += 6) {
      const origin = deform(x, y);
      const right = deform(x + 1, y);
      const down = deform(x, y + 1);
      const ratio = (1 + right.x - origin.x) * (1 + down.y - origin.y) - (down.x - origin.x) * (right.y - origin.y);
      minimum = Math.min(minimum, ratio);
    }
  }
  return minimum;
}

function movement(recipe: EditRecipe, [x, y]: readonly [number, number]) {
  const offset = deformerFor(recipe)(x, y);
  return Math.hypot(offset.x, offset.y);
}

const allControls: [Section, string, number][] = [
  ...Object.entries(faceControlRanges).flatMap(([key, range]) => [["face", key, range.min], ["face", key, range.max]] as const),
  ...Object.entries(eyeControlRanges)
    .filter(([key]) => key !== "eyeRegionScale")
    .flatMap(([key, range]) => [["eyes", key, range.min], ["eyes", key, range.max]] as const),
  ...Object.entries(browControlRanges).flatMap(([key, range]) => [["brows", key, range.min], ["brows", key, range.max]] as const),
  ...Object.entries(mouthControlRanges).flatMap(([key, range]) => [["mouth", key, range.min], ["mouth", key, range.max]] as const),
].filter(([, , value]) => value !== 0) as [Section, string, number][];

describe("deformation limits on an anime portrait", () => {
  it.each(allControls)("never folds the image at %s %s = %s", (section, key, value) => {
    expect(minimumAreaRatio(recipeWith(section, key, value))).toBeGreaterThan(0.15);
  });

  it.each(allControls.filter(([section]) => section !== "mouth"))(
    "keeps the default slider end (half of %s %s = %s) free of strong squeezing",
    (section, key, value) => {
      expect(minimumAreaRatio(recipeWith(section, key, value / 2))).toBeGreaterThan(0.4);
    },
  );

  const faceShapeControls = Object.entries(faceControlRanges)
    .filter(([key]) => !(proportionControlKeys as readonly string[]).includes(key))
    .flatMap(([key, range]) => [[key, range.min], [key, range.max]] as const)
    .filter(([, value]) => value !== 0);

  it.each(faceShapeControls)("keeps every feature in place for face shape %s = %s", (key, value) => {
    const recipe = recipeWith("face", key, value);

    for (const feature of Object.values(animeFaceFeatures)) {
      expect(movement(recipe, feature)).toBeLessThan(0.05);
    }
  });

  // Directions and strengths follow the reference retouching app at the end of its slider (stored 0.2).
  it("narrows the face along its outline for positive face width", () => {
    const deform = deformerFor(recipeWith("face", "faceWidth", 0.2));

    expect(deform(285, 790).x).toBeGreaterThan(6);
    expect(deform(636, 770).x).toBeLessThan(-6);
    expect(deform(285, 790).y).toBeLessThan(0);
  });

  it("shortens the chin by lifting its tip for positive chin length", () => {
    expect(deformerFor(recipeWith("face", "chinLength", 0.2))(468, 903).y).toBeLessThan(-8);
    expect(deformerFor(recipeWith("face", "chinLength", -0.2))(468, 903).y).toBeGreaterThan(5);
  });

  it("keeps cheekbone edits to the outline just below the eyes", () => {
    const deform = deformerFor(recipeWith("face", "cheekbone", 0.2));

    expect(deform(236, 725).x).toBeGreaterThan(3);
    expect(Math.hypot(deform(300, 840).x, deform(300, 840).y)).toBeLessThan(1);
  });

  it("moves features for proportion edits", () => {
    expect(movement(recipeWith("face", "midFaceLength", 0.4), animeFaceFeatures.mouth)).toBeGreaterThan(20);
    expect(movement(recipeWith("face", "faceLength", 0.6), animeFaceFeatures.mouth)).toBeGreaterThan(8);
  });
});
