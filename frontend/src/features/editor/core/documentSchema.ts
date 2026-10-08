import {z} from "zod";
import {normalizedRecipeSchema} from "./recipeSchema";
import type {EditorDocument,LocalEditorDocument,SavedEditorDocument} from "./editorDocument";
const number=z.number().finite();
const recipe=normalizedRecipeSchema;
const documentShape={
  schemaVersion:z.literal(1),engineVersion:z.literal("kigcraft-pixi-1"),
  normalizedRecipe:recipe,calibrationRevision:z.string().min(1).max(100),
  annotationIntents:z.array(z.object({annotationId:z.string().min(1).max(100),intent:z.enum(["instruction","preserve","modify"]),text:z.string().max(10000)}).strict()).max(1000),
  maskDocument:z.object({coordinateSpace:z.literal("normalized-image"),radiusUnit:z.literal("image-pixel"),
    strokes:z.array(z.object({id:z.string().min(1).max(100),mode:z.enum(["brush","erase"]),radius:number.positive().max(16384),
      points:z.array(z.object({x:number.min(0).max(1),y:number.min(0).max(1)}).strict()).min(1).max(10000)}).strict()).max(10000),
    binding:z.object({sourceKey:z.string().min(1).max(100),width:number.int().positive().max(16384),height:number.int().positive().max(16384),geometry:z.string().max(1024*1024)}).strict().nullable(),
  }).strict(),
};
function coherent(doc:SavedEditorDocument,ctx:z.RefinementCtx){
  if(!normalizedRecipeSchema.safeParse(doc.normalizedRecipe).success)return;
  const marks=doc.normalizedRecipe.annotations.map(mark=>mark.id),intents=doc.annotationIntents.map(item=>item.annotationId);
  if(new Set(marks).size!==marks.length||new Set(intents).size!==intents.length||marks.length!==intents.length||marks.some(id=>!intents.includes(id)))
    ctx.addIssue({code:"custom",message:"Annotation identities mismatch"});
  if(doc.normalizedRecipe.annotations.some(mark=>mark.note!==""))ctx.addIssue({code:"custom",message:"Instructions must be separate from geometry"});
  if(new Set(doc.maskDocument.strokes.map(stroke=>stroke.id)).size!==doc.maskDocument.strokes.length)ctx.addIssue({code:"custom",message:"Duplicate mask strokes"});
  const coords=doc.normalizedRecipe.imageCoordinates;
  if(coords?.width!==doc.root.width||coords?.height!==doc.root.height)ctx.addIssue({code:"custom",message:"Root dimensions mismatch"});
}
const rootMetadata={sha256:z.string().regex(/^[a-f0-9]{64}$/),width:number.int().positive().max(16384),height:number.int().positive().max(16384)};
export const editorDocumentSchema:z.ZodType<EditorDocument>=z.object({...documentShape,
  root:z.object({...rootMetadata,assetId:z.string().min(1)}).strict(),baseRevision:z.string().min(1),
}).strict().superRefine((doc,ctx)=>{
  coherent(doc,ctx);
  const mask=doc.maskDocument,binding=mask.binding;
  if((mask.strokes.length&&!binding)||(binding&&(binding.sourceKey!==doc.root.assetId||binding.width!==doc.root.width||binding.height!==doc.root.height)))
    ctx.addIssue({code:"custom",message:"Mask source mismatch"});
});
export const localEditorDocumentSchema:z.ZodType<LocalEditorDocument>=z.object({...documentShape,
  root:z.object({...rootMetadata,kind:z.literal("local"),localId:z.string().uuid()}).strict(),
}).strict().superRefine((doc,ctx)=>{
  coherent(doc,ctx);
  const mask=doc.maskDocument,binding=mask.binding;
  if((mask.strokes.length&&!binding)||(binding&&(binding.sourceKey!=="local:"+doc.root.localId||binding.width!==doc.root.width||binding.height!==doc.root.height)))
    ctx.addIssue({code:"custom",message:"Local mask source mismatch"});
});
