import { anchorRecipeToImage } from "./core/imageCoordinates";
import { describe, expect, it } from "vitest";
import { createEmptyRecipe, updateEyeControl, updateFaceControl, updateMouthControl } from "./deformation/recipe";
import { embedEditRecipeInPng, readEditRecipeFromImage } from "./imageRecipeMetadata";

const EMPTY_PNG = new Uint8Array([
  137, 80, 78, 71, 13, 10, 26, 10,
  0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96, 130,
]);

describe("image edit recipe metadata", () => {
  it("round-trips the complete edit recipe through a PNG iTXt chunk", async () => {
    const recipe = updateMouthControl(
      updateEyeControl(updateFaceControl(createEmptyRecipe(), "faceWidth", -0.24), "eyeSize", 0.18),
      "mouthSmile",
      0.04,
    );
    recipe.annotations = [
      {
        endX: 0.6,
        endY: 0.45,
        height: 0.1,
        id: "annotation-1",
        kind: "pin",
        note: "保留左眼高光",
        width: 0.1,
        x: 0.42,
        y: 0.4,
      },
    ];
    recipe.details.accessoryNote = "不要改变发饰";
    recipe.liquify = [{ deltaX: 0.02, deltaY: -0.01, mode: "warp", radius: 48, strength: 0.7, x: 0.5, y: 0.6 }];

    const embedded = await embedEditRecipeInPng(new Blob([EMPTY_PNG], { type: "image/png" }), recipe);

    await expect(readEditRecipeFromImage(embedded)).resolves.toEqual(recipe);
  });

  it("replaces an existing recipe and ignores images without supported metadata", async () => {
    const first = updateFaceControl(createEmptyRecipe(), "faceWidth", -0.1);
    const second = updateFaceControl(createEmptyRecipe(), "faceWidth", 0.3);
    const once = await embedEditRecipeInPng(new Blob([EMPTY_PNG], { type: "image/png" }), first);
    const twice = await embedEditRecipeInPng(once, second);

    await expect(readEditRecipeFromImage(twice)).resolves.toEqual(second);
    await expect(readEditRecipeFromImage(new Blob(["jpeg"], { type: "image/jpeg" }))).resolves.toBeNull();
    await expect(readEditRecipeFromImage(new Blob([PNG_SIGNATURE_ONLY], { type: "image/png" }))).resolves.toBeNull();
  });
  it("keeps anchored image units when embedding and reading a saved PNG", async () => {
    const recipe = anchorRecipeToImage(createEmptyRecipe(), 2048, 1024, 4);
    recipe.liquify = [{ mode: "scale", radius: 480, strength: .3, scale: .3, x: .4, y: .5 }];
    const blob = await embedEditRecipeInPng(new Blob([EMPTY_PNG], { type: "image/png" }), recipe);
    expect(await readEditRecipeFromImage(blob)).toEqual(recipe);
  });

});

const PNG_SIGNATURE_ONLY = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
