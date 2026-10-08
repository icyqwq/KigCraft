import {normalizeEditRecipe,type EditRecipe} from "../deformation/recipe";
import type {EditorHistoryState} from "./history";
import type {MaskBinding} from "./maskBinding";
import type {LocalMaskStroke} from "../localGeneration";

type DocumentState = {
  schemaVersion:1;engineVersion:"kigcraft-pixi-1";
  normalizedRecipe:EditRecipe;
  calibrationRevision:string;
  annotationIntents:{annotationId:string;intent:"instruction"|"preserve"|"modify";text:string}[];
  maskDocument:{coordinateSpace:"normalized-image";radiusUnit:"image-pixel";strokes:LocalMaskStroke[];binding:MaskBinding|null};
};
type RootMetadata={sha256:string;width:number;height:number};
export type EditorDocument = DocumentState & {
  root:RootMetadata & {assetId:string};
  baseRevision:string;
};
export type LocalEditorDocument = DocumentState & {
  root:RootMetadata & {kind:"local";localId:string};
};
export type SavedEditorDocument=EditorDocument|LocalEditorDocument;

function ordered(value:unknown):unknown {
  if(Array.isArray(value))return value.map(ordered);
  if(value&&typeof value==="object")return Object.fromEntries(Object.entries(value).filter(([,item])=>item!==undefined).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>[key,ordered(item)]));
  return value;
}
async function calibrationId(recipe:EditRecipe){
  const value=JSON.stringify(ordered({landmarks:recipe.landmarks??null,coordinates:recipe.imageCoordinates}));
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(value))),byte=>byte.toString(16).padStart(2,"0")).join("");
}
async function captureDocumentState(snapshot:EditorHistoryState,root:RootMetadata,previous?:SavedEditorDocument):Promise<DocumentState>{
  const frozen=structuredClone(snapshot);
  const recipe=normalizeEditRecipe(frozen.recipe);
  const coords=recipe.imageCoordinates;
  if(!coords||coords.width!==root.width||coords.height!==root.height)throw new Error("Editor coordinates do not match root");
  const intents=recipe.annotations.map(mark=>({annotationId:mark.id,
    intent:previous?.annotationIntents.find(item=>item.annotationId===mark.id)?.intent??"instruction" as const,text:mark.note}));
  const normalizedRecipe={...recipe,annotations:recipe.annotations.map(mark=>({...mark,note:""}))};
  return {schemaVersion:1,engineVersion:"kigcraft-pixi-1",normalizedRecipe,
    calibrationRevision:await calibrationId(recipe),annotationIntents:intents,
    maskDocument:{coordinateSpace:"normalized-image",radiusUnit:"image-pixel",strokes:frozen.maskStrokes,binding:frozen.maskBinding??null}};
}
/** The captured value owns all nested geometry, metadata and mask data before the first await. */
export async function createEditorDocument(snapshot:EditorHistoryState,root:EditorDocument["root"],baseRevision:string,previous?:EditorDocument):Promise<EditorDocument>{
  const source=structuredClone(root);
  return {...await captureDocumentState(snapshot,source,previous),root:source,baseRevision};
}
export async function createLocalEditorDocument(snapshot:EditorHistoryState,root:LocalEditorDocument["root"],previous?:LocalEditorDocument):Promise<LocalEditorDocument>{
  const source=structuredClone(root);
  return {...await captureDocumentState(snapshot,source,previous),root:source};
}
export function editorStateFromDocument(document:SavedEditorDocument):EditorHistoryState {
  if(document.schemaVersion!==1||document.engineVersion!=="kigcraft-pixi-1")throw new Error("Unsupported editor document");
  const value=structuredClone(document);
  return {recipe:{...value.normalizedRecipe,annotations:value.normalizedRecipe.annotations.map(mark=>({
    ...mark,note:value.annotationIntents.find(item=>item.annotationId===mark.id)?.text??""}))},
    maskStrokes:value.maskDocument.strokes,...(value.maskDocument.binding?{maskBinding:value.maskDocument.binding}:{})};
}
