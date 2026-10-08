import type {Revision} from "./api";
import type {EditorDocument} from "../editor/core/editorDocument";
import {normalizeEditRecipe,type EditRecipe} from "../editor/deformation/recipe";

function ordered(value:unknown):unknown {
  if(Array.isArray(value))return value.map(ordered);
  if(value&&typeof value==="object")return Object.fromEntries(Object.entries(value)
    .filter(([,item])=>item!==undefined).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>[key,ordered(item)]));
  return value;
}
function pixels(recipe:EditRecipe) {
  return {face:recipe.face,eyes:recipe.eyes,brows:recipe.brows,mouth:recipe.mouth,liquify:recipe.liquify,
    landmarks:recipe.landmarks,imageCoordinates:recipe.imageCoordinates};
}
function noDeformation(recipe:EditRecipe) {
  return Object.values(recipe.face).every(value=>value===0)&&Object.values(recipe.mouth).every(value=>value===0)&&
    Object.values(recipe.brows).every(value=>value===0)&&
    Object.entries(recipe.eyes).every(([key,value])=>key==="eyeRegionScale"||value===0)&&recipe.liquify.length===0;
}
/** Preserve already published pixels when only non-raster editor state changed. */
export function unchangedEditorImage(revision:Revision,document:EditorDocument):string|undefined {
  if(document.baseRevision!==revision.id||document.root.assetId!==revision.root_source_asset_id)return;
  const next=document.normalizedRecipe;
  // A pristine source needs no mesh resampling, regardless of calibration or annotation changes.
  if(revision.asset_id===revision.root_source_asset_id&&noDeformation(next))return revision.asset_id;
  if(!revision.recipe)return;
  const previous=normalizeEditRecipe(revision.recipe as Partial<EditRecipe>);
  if(JSON.stringify(ordered(pixels(previous)))===JSON.stringify(ordered(pixels(next))))return revision.asset_id;
}
