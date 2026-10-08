import { describe, expect, it } from "vitest";
import { anchorRecipeToImage } from "./imageCoordinates";
import { createEmptyRecipe, normalizeEditRecipe, updateLiquifyBrushPair, updateLiquifyScaleBrush } from "../deformation/recipe";

describe("image coordinate recipes", () => {
  it("converts a legacy radius once and preserves it through normalization and reopening", () => {
    const original = { ...createEmptyRecipe(), liquify: [
      { mode: "warp" as const, radius: 72, strength: .2, x: .4, y: .5, deltaX: .05, deltaY: 0 },
    ] };
    const anchored = anchorRecipeToImage(original, 2048, 1024, 4);
    expect(anchored.liquify[0].radius).toBe(288);
    expect(original.liquify[0].radius).toBe(72);
    const reopened = normalizeEditRecipe(JSON.parse(JSON.stringify(anchored)));
    expect(anchorRecipeToImage(reopened, 2048, 1024, 2)).toEqual(anchored);
    expect(() => anchorRecipeToImage(reopened, 1024, 1024)).toThrow("different image size");
  });

  it("stores brush and mirrored radii in image pixels without clamping them to CSS limits", () => {
    const recipe = anchorRecipeToImage(createEmptyRecipe(), 2048, 1024, 4);
    const warped = updateLiquifyBrushPair(recipe, { mode: "warp", radius: 120, strength: .2,
      x: .4, y: .5, deltaX: .05, deltaY: 0 }, .5);
    expect(warped.liquify.map(stroke => stroke.radius)).toEqual([480, 480]);
    expect(warped.liquify.map(stroke => stroke.deltaX)).toEqual([.05, -.05]);
    const scaled = updateLiquifyScaleBrush(warped, { radius: 150, scale: .5 });
    expect(scaled.liquify.at(-1)?.radius).toBe(600);
    expect(scaled.imageCoordinates).toEqual(recipe.imageCoordinates);
  });

  it.each([0, -1, NaN, Infinity])("rejects invalid coordinate scale %s", effectScale => {
    expect(() => anchorRecipeToImage(createEmptyRecipe(), 1024, 1024, effectScale)).toThrow();
  });
});
