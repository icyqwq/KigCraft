import { createEmptyRecipe } from "../deformation/recipe";
import { readEditRecipeFromImage } from "../imageRecipeMetadata";

/** A PNG contains finished pixels, not the original root image needed to replay deformation. */
export async function prepareFlatImageImport(file: Blob) {
  return {
    recipe: createEmptyRecipe(),
    sourceRecipe: await readEditRecipeFromImage(file),
  };
}
