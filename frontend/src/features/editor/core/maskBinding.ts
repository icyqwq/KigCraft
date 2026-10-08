import type { EditRecipe } from "../deformation/recipe";

export type MaskBinding = { sourceKey: string; width: number; height: number; geometry: string };

export function bindMask(recipe: EditRecipe, sourceKey: string, width: number, height: number): MaskBinding {
  return { sourceKey, width, height, geometry: JSON.stringify({
    face: recipe.face, eyes: recipe.eyes, brows: recipe.brows, mouth: recipe.mouth, liquify: recipe.liquify,
    landmarks: recipe.landmarks ?? null, coordinates: recipe.imageCoordinates ?? null,
  }) };
}

// JSON object key order is not geometry. Array order and every value remain significant.
function equalGeometryValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const a = left as Record<string, unknown>, b = right as Record<string, unknown>;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length &&
    keys.every(key => Object.prototype.hasOwnProperty.call(b, key) && equalGeometryValue(a[key], b[key]));
}
function sameGeometry(left: string, right: string): boolean {
  if (left === right) return true;
  try { return equalGeometryValue(JSON.parse(left), JSON.parse(right)); }
  catch { return false; }
}

export function maskMatches(
  binding: MaskBinding | undefined, recipe: EditRecipe, sourceKey: string, width: number, height: number,
) {
  if (!binding) return false;
  const expected = bindMask(recipe, sourceKey, width, height);
  return binding.sourceKey === expected.sourceKey && binding.width === expected.width &&
    binding.height === expected.height && sameGeometry(binding.geometry, expected.geometry);
}
