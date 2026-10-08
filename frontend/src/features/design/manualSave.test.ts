import {beforeEach,afterEach,expect,it,vi} from "vitest";
import {webcrypto} from "node:crypto";
import {ManualEditorSave} from "./manualSave";
import {designApi,DesignApiError,type EditorSaveResult} from "./api";
import {createEditorDocument} from "../editor/core/editorDocument";
import {createEmptyRecipe} from "../editor/deformation/recipe";
const target={ownerId:"owner",branchId:"branch",baseRevisionId:"base",rootAssetId:"root",branchVersion:3,historical:false};
const signal=()=>new AbortController().signal;
async function doc(){
  const recipe=createEmptyRecipe();recipe.imageCoordinates={version:1,width:32,height:32,effectScale:1};
  return createEditorDocument({recipe,maskStrokes:[]},{assetId:"root",sha256:"a".repeat(64),width:32,height:32},"base");
}
async function result():Promise<EditorSaveResult>{
  return {disposition:"checkpoint",checkpoint:{id:"checkpoint",version:1,branch_id:"branch",revision_id:"base",clean_asset_id:"root",document:await doc(),created_at:""},
    revision:{id:"base",branch_id:"branch",parent_revision_id:null,asset_id:"root",root_source_asset_id:"root",root_image_url:"/root",image_url:"/root",operation_id:null,source_kind:"ai",view:"front",created_at:"",recipe:null}};
}
beforeEach(async()=>{
  vi.stubGlobal("crypto",webcrypto);sessionStorage.clear();
  vi.spyOn(designApi,"upload").mockResolvedValue({id:"upload",image_url:"",width:32,height:32,status:"ready"});
  vi.spyOn(designApi,"saveEditor").mockResolvedValue(await result());
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});
async function prepared(){const adapter=new ManualEditorSave(target);await adapter.prepare(await doc(),new Blob(["png"]),"previous",signal());return adapter;}
it("freezes before uploading and sends only after durable receipt",async()=>{
  const document=await doc();let finish!:(value:any)=>void;
  vi.mocked(designApi.upload).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const adapter=new ManualEditorSave(target),waiting=adapter.prepare(document,new Blob(["png"]),null,signal());
  document.normalizedRecipe.face.faceWidth=.2;
  finish({id:"upload",image_url:"",width:32,height:32,status:"ready"});await waiting;
  expect(designApi.saveEditor).not.toHaveBeenCalled();
  await adapter.submit(signal());
  const payload=vi.mocked(designApi.saveEditor).mock.calls[0][1];
  expect(payload.document.normalizedRecipe.face.faceWidth).toBe(0);expect(payload.expected_branch_version).toBe(3);
  expect(designApi.upload).toHaveBeenCalledWith(expect.any(Blob),"owner",expect.any(AbortSignal));
});
it("survives lost acknowledgement and reload with the exact request and no extra upload",async()=>{
  const first=await prepared();
  vi.mocked(designApi.saveEditor).mockRejectedValueOnce(new TypeError("lost response"));
  await expect(first.submit(signal())).rejects.toThrow();
  const next=new ManualEditorSave(target);next.load();
  expect(designApi.saveEditor).toHaveBeenCalledTimes(1);
  await next.submit(signal());
  expect(vi.mocked(designApi.saveEditor).mock.calls[0].slice(0,2)).toEqual(vi.mocked(designApi.saveEditor).mock.calls[1].slice(0,2));
  expect(designApi.upload).toHaveBeenCalledTimes(1);
});
it("storage failure prevents dispatch",async()=>{
  const adapter=await prepared();
  vi.spyOn(Storage.prototype,"setItem").mockImplementationOnce(()=>{throw new Error("quota");});
  await expect(adapter.submit(signal())).rejects.toThrow("quota");
  expect(designApi.saveEditor).not.toHaveBeenCalled();
  await adapter.submit(signal());expect(designApi.saveEditor).toHaveBeenCalledTimes(1);
});
it("confirmed result recovers without another HTTP request until cleanup is acknowledged",async()=>{
  const adapter=await prepared();await adapter.submit(signal());
  const next=new ManualEditorSave(target);expect(next.load()?.phase).toBe("confirmed");
  await next.submit(signal());expect(designApi.saveEditor).toHaveBeenCalledTimes(1);
  next.acknowledge();expect(new ManualEditorSave(target).load()).toBeNull();
});
it("a failed confirmation write retains attempted identity for retry",async()=>{
  const adapter=await prepared();
  vi.mocked(designApi.saveEditor).mockImplementationOnce(async()=>{
    vi.spyOn(Storage.prototype,"setItem").mockImplementationOnce(()=>{throw new Error("quota");});
    return result();
  });
  await expect(adapter.submit(signal())).rejects.toThrow("quota");
  const next=new ManualEditorSave(target);expect(next.load()).toMatchObject({phase:"prepared",attempted:true});
  await next.submit(signal());expect(designApi.upload).toHaveBeenCalledTimes(1);
  expect(vi.mocked(designApi.saveEditor).mock.calls[0].slice(0,2)).toEqual(vi.mocked(designApi.saveEditor).mock.calls[1].slice(0,2));
});
it("does not replace uncertain requests with later edits and retains login/idempotency failures",async()=>{
  const adapter=await prepared();
  vi.mocked(designApi.saveEditor).mockRejectedValueOnce(new DesignApiError("authentication_required",401));
  await expect(adapter.submit(signal())).rejects.toThrow();
  await expect(adapter.prepare(await doc(),new Blob(["later"]),null,signal())).rejects.toThrow("previous");
  vi.mocked(designApi.saveEditor).mockRejectedValueOnce(new DesignApiError("idempotency_conflict",409));
  await expect(adapter.submit(signal())).rejects.toThrow();expect(adapter.load()).toMatchObject({attempted:true});
});
it("definite checkpoint rejection allows a later independently prepared request",async()=>{
  const adapter=await prepared();
  vi.mocked(designApi.saveEditor).mockRejectedValueOnce(new DesignApiError("editor_checkpoint_conflict",409));
  await expect(adapter.submit(signal())).rejects.toThrow();expect(adapter.load()).toBeNull();
});
it("isolates owners and rejects a moved receipt",async()=>{
  const adapter=await prepared();const other=new ManualEditorSave({...target,ownerId:"other"});
  expect(other.load()).toBeNull();
  sessionStorage.setItem(other.key,sessionStorage.getItem(adapter.key)!);
  expect(()=>other.load()).toThrow("Unsupported");
});

it("keeps an inconsistent confirmed receipt for recovery instead of accepting it",async()=>{
  const adapter=await prepared();await adapter.submit(signal());
  const stored=JSON.parse(sessionStorage.getItem(adapter.key)!);
  stored.ticket.result.checkpoint.document.baseRevision="other-revision";
  const corrupted=JSON.stringify(stored);sessionStorage.setItem(adapter.key,corrupted);
  expect(()=>new ManualEditorSave(target).load()).toThrow();
  expect(sessionStorage.getItem(adapter.key)).toBe(corrupted);
  expect(designApi.saveEditor).toHaveBeenCalledTimes(1);
});
it("rejects a confirmed checkpoint paired with an unrelated clean image",async()=>{
  const adapter=await prepared();await adapter.submit(signal());
  const stored=JSON.parse(sessionStorage.getItem(adapter.key)!);
  stored.ticket.result.revision.asset_id="other-image";
  sessionStorage.setItem(adapter.key,JSON.stringify(stored));
  expect(()=>new ManualEditorSave(target).load()).toThrow();
  expect(designApi.saveEditor).toHaveBeenCalledTimes(1);
});

it("closing during upload cannot persist or submit a new request",async()=>{
  const abort=new AbortController();
  vi.mocked(designApi.upload).mockImplementationOnce(async()=>{abort.abort();return {id:"upload",image_url:"",width:32,height:32,status:"ready"};});
  const adapter=new ManualEditorSave(target);
  await expect(adapter.prepare(await doc(),new Blob(["png"]),null,abort.signal)).rejects.toThrow();
  expect(adapter.load()).toBeNull();expect(designApi.saveEditor).not.toHaveBeenCalled();
});

it("saves unchanged published pixels without uploading the resampled export",async()=>{
 const adapter=new ManualEditorSave(target);
 await adapter.prepare(await doc(),new Blob(["resampled"]),null,signal(),"root");
 await adapter.submit(signal());
 expect(designApi.upload).not.toHaveBeenCalled();
 expect(vi.mocked(designApi.saveEditor).mock.calls[0][1].clean_asset_id).toBe("root");
});
it("does not persist an unchanged-image save after cancellation",async()=>{
 const controller=new AbortController();controller.abort();
 const adapter=new ManualEditorSave(target);
 await expect(adapter.prepare(await doc(),new Blob(["unused"]),null,controller.signal,"root")).rejects.toThrow();
 expect(adapter.load()).toBeNull();expect(designApi.upload).not.toHaveBeenCalled();expect(designApi.saveEditor).not.toHaveBeenCalled();
});
