import {expect,it} from "vitest";
import {unchangedEditorImage} from "./unchangedEditorImage";
import type {Revision} from "./api";
import {createEditorDocument} from "../editor/core/editorDocument";
import {createEmptyRecipe} from "../editor/deformation/recipe";
import {createDefaultLandmarks} from "../editor/deformation/landmarks";
const revision:Revision={id:"base",branch_id:"branch",parent_revision_id:null,asset_id:"root",root_source_asset_id:"root",
 root_image_url:"/root",image_url:"/root",operation_id:null,source_kind:"ai",view:"front",created_at:"",recipe:null};
async function document(){
 const recipe=createEmptyRecipe();recipe.imageCoordinates={version:1,width:32,height:32,effectScale:1};
 return createEditorDocument({recipe,maskStrokes:[]},{assetId:"root",sha256:"a".repeat(64),width:32,height:32},"base");
}
it("keeps pristine pixels with annotations, calibration, masks and instruction changes",async()=>{
 const doc=await document();doc.normalizedRecipe.landmarks=createDefaultLandmarks(1,1);
 doc.normalizedRecipe.eyes.eyeRegionScale=100;
 doc.normalizedRecipe.details.accessoryNote="keep";
 doc.annotationIntents=[{annotationId:"note",intent:"instruction",text:"Keep eyes"}];
 expect(unchangedEditorImage(revision,doc)).toBe("root");
});
it.each(["face","eyes","mouth","liquify"] as const)("renders actual %s changes",async kind=>{
 const doc=await document();
 if(kind==="face")doc.normalizedRecipe.face.faceWidth=.1;
 if(kind==="eyes")doc.normalizedRecipe.eyes.eyeSize=.1;
 if(kind==="mouth")doc.normalizedRecipe.mouth.mouthSmile=.05;
 if(kind==="liquify")doc.normalizedRecipe.liquify=[{mode:"scale",x:.5,y:.5,radius:12,strength:.5,scale:1.1}];
 expect(unchangedEditorImage(revision,doc)).toBeUndefined();
});
it("keeps an edited image only when every raster input is unchanged",async()=>{
 const doc=await document();doc.normalizedRecipe.face.faceWidth=.1;
 const base={...revision,asset_id:"edited",recipe:structuredClone(doc.normalizedRecipe)};
 doc.normalizedRecipe.details.accessoryNote="new instruction";
 expect(unchangedEditorImage(base,doc)).toBe("edited");
 doc.normalizedRecipe.imageCoordinates!.effectScale=2;
 expect(unchangedEditorImage(base,doc)).toBeUndefined();
 doc.normalizedRecipe.imageCoordinates!.effectScale=1;doc.normalizedRecipe.landmarks=createDefaultLandmarks(1,1);
 expect(unchangedEditorImage(base,doc)).toBeUndefined();
});
it("does not reuse unrelated or unknown edited pixels, including reset edits",async()=>{
 const doc=await document();
 expect(unchangedEditorImage({...revision,asset_id:"edited"},doc)).toBeUndefined();
 const recipe=createEmptyRecipe();recipe.face.faceWidth=.1;
 expect(unchangedEditorImage({...revision,asset_id:"edited",recipe},doc)).toBeUndefined();
 doc.baseRevision="elsewhere";expect(unchangedEditorImage(revision,doc)).toBeUndefined();
 doc.baseRevision="base";doc.root.assetId="elsewhere";expect(unchangedEditorImage(revision,doc)).toBeUndefined();
});
