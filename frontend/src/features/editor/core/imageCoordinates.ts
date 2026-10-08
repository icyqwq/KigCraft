import type { EditRecipe } from "../deformation/recipe";

/** Versioned units for replaying the existing deformation engine without a viewport dependency. */
export type ImageCoordinates = {
  version: 1;
  width: number;
  height: number;
  /** Source pixels per legacy algorithm unit. Stored, never recalculated on resize. */
  effectScale: number;
};

export function validImageCoordinates(value: ImageCoordinates | undefined): value is ImageCoordinates {
  return Boolean(value && value.version === 1 &&
    Number.isInteger(value.width) && value.width > 0 && value.width <= 16384 &&
    Number.isInteger(value.height) && value.height > 0 && value.height <= 16384 &&
    Number.isFinite(value.effectScale) && value.effectScale > 0 && value.effectScale <= 16384);
}

/** Convert legacy CSS radii once; the original recipe is never mutated. */
export function anchorRecipeToImage(
  recipe: EditRecipe, width: number, height: number, effectScale = Math.max(width, height) / 720,
): EditRecipe {
  const coordinates: ImageCoordinates = { version: 1, width, height, effectScale };
  if (!validImageCoordinates(coordinates)) throw new Error("Invalid image coordinates");
  if (recipe.imageCoordinates) {
    if (!validImageCoordinates(recipe.imageCoordinates) ||
        recipe.imageCoordinates.width !== width || recipe.imageCoordinates.height !== height) {
      throw new Error("Recipe belongs to a different image size");
    }
    return recipe;
  }
  return {
    ...recipe,
    imageCoordinates: coordinates,
    liquify: recipe.liquify.map(stroke => ({ ...stroke, radius: stroke.radius * effectScale })),
  };
}
