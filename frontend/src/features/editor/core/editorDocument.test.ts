import {afterEach,expect,it,vi} from "vitest";
import {webcrypto} from "node:crypto";
import {createEditorDocument,editorStateFromDocument} from "./editorDocument";
import {createEmptyRecipe} from "../deformation/recipe";
import {EditorHistory} from "./history";
const root={assetId:"root",sha256:"a".repeat(64),width:32,height:32};
function state(){
  const recipe=createEmptyRecipe();recipe.imageCoordinates={version:1,width:32,height:32,effectScale:1};
  recipe.annotations=[{id:"a",kind:"pin",x:.2,y:.3,width:.1,height:.1,endX:.4,endY:.5,note:"保留细节"}];
  return {recipe,maskStrokes:[]};
}
afterEach(()=>vi.unstubAllGlobals());
it("keeps one annotation geometry and roundtrips its separate instruction",async()=>{
  vi.stubGlobal("crypto",webcrypto);
  const snapshot=state(),document=await createEditorDocument(snapshot,root,"base");
  expect(document.normalizedRecipe.annotations[0].note).toBe("");
  expect(document.annotationIntents).toEqual([{annotationId:"a",intent:"instruction",text:"保留细节"}]);
  expect(editorStateFromDocument(document)).toEqual(snapshot);
});
it("owns the frozen input before asynchronous hashing finishes",async()=>{
  vi.stubGlobal("crypto",webcrypto);
  const snapshot=state(),source={...root};
  const pending=createEditorDocument(snapshot,source,"base");
  snapshot.recipe.annotations[0].note="later";snapshot.recipe.face.faceWidth=.2;source.width=999;
  const document=await pending;
  expect(document.root.width).toBe(32);expect(document.annotationIntents[0].text).toBe("保留细节");
  expect(document.normalizedRecipe.face.faceWidth).toBe(0);
});
it("calibration identity is stable for no-op saves and changes with landmark calibration",async()=>{
  vi.stubGlobal("crypto",webcrypto);
  const snapshot=state(),first=await createEditorDocument(snapshot,root,"base");
  expect((await createEditorDocument(snapshot,root,"base")).calibrationRevision).toBe(first.calibrationRevision);
  const {createDefaultLandmarks}=await import("../deformation/landmarks");
  snapshot.recipe.landmarks=createDefaultLandmarks(1,1);
  expect((await createEditorDocument(snapshot,root,"base")).calibrationRevision).not.toBe(first.calibrationRevision);
});
it("rejects mismatched export coordinates",async()=>{
  vi.stubGlobal("crypto",webcrypto);
  await expect(createEditorDocument(state(),{...root,width:64},"base")).rejects.toThrow("coordinates");
});
it("restored state cannot mutate an immutable checkpoint",async()=>{
  vi.stubGlobal("crypto",webcrypto);
  const document=await createEditorDocument(state(),root,"base");
  const restored=editorStateFromDocument(document);restored.recipe.annotations[0].x=.8;
  expect(document.normalizedRecipe.annotations[0].x).toBe(.2);
});

it("local documents use a device handle without inventing a cloud asset or revision",async()=>{
  vi.stubGlobal("crypto",webcrypto);
  const {createLocalEditorDocument}=await import("./editorDocument");
  const {localEditorDocumentSchema,editorDocumentSchema}=await import("./documentSchema");
  const localRoot={kind:"local" as const,localId:"28a8e203-bca6-4cf2-8616-c6c0b3decb55",sha256:root.sha256,width:32,height:32};
  const snapshot=state(),pending=createLocalEditorDocument(snapshot,localRoot);
  snapshot.recipe.face.faceWidth=.2;localRoot.width=99;
  const document=await pending;
  expect(localEditorDocumentSchema.parse(document)).toEqual(document);
  expect(document.root.width).toBe(32);expect(document.normalizedRecipe.face.faceWidth).toBe(0);
  expect(document).not.toHaveProperty("baseRevision");expect(document.root).not.toHaveProperty("assetId");
  expect(editorDocumentSchema.safeParse(document).success).toBe(false);
  expect(editorStateFromDocument(document).recipe.annotations[0].note).toBe("保留细节");
});
it("local masks cannot be rebound to a different device image",async()=>{
  vi.stubGlobal("crypto",webcrypto);
  const {createLocalEditorDocument}=await import("./editorDocument");
  const {localEditorDocumentSchema}=await import("./documentSchema");
  const document=await createLocalEditorDocument(state(),{kind:"local",localId:"28a8e203-bca6-4cf2-8616-c6c0b3decb55",sha256:root.sha256,width:32,height:32});
  document.maskDocument.strokes=[{id:"stroke",mode:"brush",radius:4,points:[{x:.5,y:.5}]}];
  expect(localEditorDocumentSchema.safeParse(document).success).toBe(false);
  document.maskDocument.binding={sourceKey:"different",width:32,height:32,geometry:"{}"};
  expect(localEditorDocumentSchema.safeParse(document).success).toBe(false);
  document.maskDocument.binding.sourceKey="local:"+document.root.localId;
  expect(localEditorDocumentSchema.safeParse(document).success).toBe(true);
});

it("rejects malformed or incomplete nested engine recipes without throwing from safeParse",async()=>{
  vi.stubGlobal("crypto",webcrypto);
  const {editorDocumentSchema}=await import("./documentSchema");
  const document=await createEditorDocument(state(),root,"base");
  for(const value of [null,{},[],{...document.normalizedRecipe,annotations:null},
    {...document.normalizedRecipe,eyes:{eyeSize:0}},
    {...document.normalizedRecipe,face:{...document.normalizedRecipe.face,faceWidth:Infinity}},
    {...document.normalizedRecipe,details:{...document.normalizedRecipe.details,script:"unexpected"}}]){
    expect(editorDocumentSchema.safeParse({...document,normalizedRecipe:value}).success).toBe(false);
  }
});

it("history version detects changes even if undone to the original pixels during export",()=>{
  const history=new EditorHistory(state()),initialVersion=history.version;
  const changed=history.snapshot;changed.recipe.face.faceWidth=.2;history.apply(changed);
  history.undo();expect(history.snapshot).toEqual(state());expect(history.version).toBeGreaterThan(initialVersion);
  const current=history.version;history.apply(history.snapshot);history.commit();expect(history.version).toBe(current);
});
