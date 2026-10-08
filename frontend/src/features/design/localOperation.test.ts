import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { LocalEditorOperation, type LocalTarget } from "./localOperation";
import { designApi, DesignApiError } from "./api";
import { createEmptyRecipe } from "../editor/deformation/recipe";
import type { EditorLocalGeneratePayload } from "../editor/localGeneration";
const target:LocalTarget={ownerId:"owner",branchId:"branch",baseRevisionId:"base",rootAssetId:"root",branchVersion:7,historical:true};
const source={sha256:"a".repeat(64),width:160,height:160};
const signal=()=>new AbortController().signal;
function input():EditorLocalGeneratePayload {
  return {baseImageBlob:new Blob(["base"]),maskImageBlob:new Blob(["mask"]),editNote:"微笑",
    localEditMode:"lock_outside",recipe:createEmptyRecipe(),selectedReferenceKeys:["old-ref"],
    uploadedReferences:[{file:new File(["ref"],"reference.png"),description:"眼睛颜色"}]};
}
const references=[{key:"old-ref",label:"参考图",description:"保留饰品"}];
beforeEach(()=>{
  sessionStorage.clear();
  let index=0;
  vi.spyOn(designApi,"upload").mockImplementation(async()=>({id:"asset-"+(++index),image_url:"",width:160,height:160,status:"ready"}));
  vi.spyOn(designApi,"quote").mockResolvedValue({id:"quote-1",max_units:1,expires_at:""});
  vi.spyOn(designApi,"submit").mockResolvedValue({id:"op",branch_id:"fork",kind:"local_edit",state:"queued",
    base_revision_id:"base",reserved_units:1,settled_units:0,error_code:null});
});
afterEach(()=>vi.restoreAllMocks());
it("freezes prepared master, mode and both selected/uploaded references before the first await",async()=>{
  const service=new LocalEditorOperation(target), payload=input();
  const pending=service.prepare(payload,source,references,signal());
  payload.editNote="later"; payload.localEditMode="preserve"; payload.recipe.annotations.push({note:"later"} as any);
  payload.uploadedReferences[0].description="later"; references[0].description="changed later";
  await pending;
  references[0].description="保留饰品";
  const request=vi.mocked(designApi.quote).mock.calls[0][1];
  expect(request).toMatchObject({text:"微笑",local_edit_mode:"lock_outside",base_revision_id:"base",expected_branch_version:7,
    continuation:null,inline_local:true,mask_asset_id:"asset-2",
    prepared_edit:{clean_asset_id:"asset-1",root_source_asset_id:"root",root_sha256:source.sha256,width:160,height:160,
      recipe:{annotations:[]}},attachments:[{asset_id:"old-ref",usage:"保留饰品"},{asset_id:"asset-3",usage:"眼睛颜色"}]});
  expect(designApi.upload).toHaveBeenCalledTimes(3);
  expect(vi.mocked(designApi.upload).mock.calls.every(call=>call[1]==="owner" && call[2] instanceof AbortSignal)).toBe(true);
  expect(designApi.submit).not.toHaveBeenCalled();
});
it("lost acknowledgement survives remount and retries the same quote and key",async()=>{
  const service=new LocalEditorOperation(target);
  await service.prepare(input(),source,references,signal());
  vi.mocked(designApi.submit).mockRejectedValueOnce(new TypeError("lost ACK"));
  await expect(service.submit(signal())).rejects.toThrow("lost ACK");
  expect(service.discardQuote()).toBe(false);
  const recovered=new LocalEditorOperation({...target,branchVersion:8});
  expect(recovered.load()).toMatchObject({phase:"quoted",attempted:true});
  await recovered.submit(signal());
  expect(vi.mocked(designApi.submit).mock.calls[1].slice(0,2)).toEqual(vi.mocked(designApi.submit).mock.calls[0].slice(0,2));
  expect(new LocalEditorOperation(target).load()).toEqual({phase:"submitted",operationId:"op",branchId:"fork"});
  expect(designApi.quote).toHaveBeenCalledTimes(1);
  await recovered.submit(signal());
  expect(designApi.submit).toHaveBeenCalledTimes(2);
});
it("pre-dispatch persistence failure prevents a model operation",async()=>{
  const storage={getItem:vi.fn(()=>null),setItem:vi.fn(),removeItem:vi.fn()} as unknown as Storage;
  const service=new LocalEditorOperation(target,storage);
  await service.prepare(input(),source,references,signal());
  const ticket=service.ticket;
  vi.mocked(storage.getItem).mockReturnValue(JSON.stringify({schema:1,ownerId:"owner",ticket}));
  vi.mocked(storage.setItem).mockImplementation(()=>{throw new Error("disk full");});
  await expect(service.submit(signal())).rejects.toThrow("disk full");
  expect(designApi.submit).not.toHaveBeenCalled();
});
it("double clicks cannot quote or submit twice",async()=>{
  const service=new LocalEditorOperation(target);
  await Promise.all([service.prepare(input(),source,references,signal()),service.prepare(input(),source,references,signal())]);
  await Promise.all([service.submit(signal()),service.submit(signal())]);
  expect(designApi.quote).toHaveBeenCalledTimes(1);
  expect(designApi.submit).toHaveBeenCalledTimes(1);
});
it("abort during upload stops subsequent uploads and quote",async()=>{
  const abort=new AbortController();
  vi.mocked(designApi.upload).mockImplementationOnce(async()=>{abort.abort();return {id:"clean",image_url:"",width:160,height:160,status:"ready"};});
  const service=new LocalEditorOperation(target);
  await expect(service.prepare(input(),source,references,abort.signal)).rejects.toThrow();
  expect(designApi.upload).toHaveBeenCalledTimes(1);
  expect(designApi.quote).not.toHaveBeenCalled();
  expect(service.load()).toBeNull();
});
it("expired authentication preserves the request while definitive rejection unlocks a new quote",async()=>{
  const service=new LocalEditorOperation(target);
  await service.prepare(input(),source,references,signal());
  vi.mocked(designApi.submit).mockRejectedValueOnce(new DesignApiError("authentication_required",401));
  await expect(service.submit(signal())).rejects.toThrow();
  expect(service.load()).toMatchObject({phase:"quoted",attempted:true});
  vi.mocked(designApi.submit).mockRejectedValueOnce(new DesignApiError("quote_invalid",409));
  await expect(service.submit(signal())).rejects.toThrow();
  expect(service.load()).toBeNull();
});
it("a missing selected reference fails before uploading and does not lock future preparation",async()=>{
  const service=new LocalEditorOperation(target);
  await expect(service.prepare(input(),source,[],signal())).rejects.toThrow("Selected reference unavailable");
  expect(designApi.upload).not.toHaveBeenCalled();
  await service.prepare(input(),source,references,signal());
  expect(service.ticket?.phase).toBe("quoted");
});
it("only a confirmed undelivered terminal operation can clear its receipt",async()=>{
  const service=new LocalEditorOperation(target);
  await service.prepare(input(),source,references,signal()); await service.submit(signal());
  const operation=await vi.mocked(designApi.submit).mock.results[0].value;
  expect(service.clearFinished(operation)).toBe(false);
  expect(service.clearFinished({...operation,state:"failed",settled_units:1})).toBe(false);
  expect(service.clearFinished({...operation,id:"foreign",state:"failed"})).toBe(false);
  expect(service.acknowledgeFinished(operation)).toBe(false);
  expect(service.clearFinished({...operation,state:"failed"})).toBe(true);
  expect(service.load()).toBeNull();
});
it("acknowledged completed delivery permits a later edit without losing the draft",async()=>{
  const service=new LocalEditorOperation(target);
  await service.prepare(input(),source,references,signal());await service.submit(signal());
  const operation=await vi.mocked(designApi.submit).mock.results[0].value;
  expect(service.acknowledgeFinished({...operation,state:"succeeded",settled_units:1})).toBe(true);
  expect(new LocalEditorOperation({...target,branchVersion:8}).load()).toBeNull();
});
it("malformed recovery is retained and never replaced by a fresh operation",async()=>{
  const service=new LocalEditorOperation(target);
  sessionStorage.setItem(service.key,"broken");
  await expect(service.prepare(input(),source,references,signal())).rejects.toThrow();
  expect(sessionStorage.getItem(service.key)).toBe("broken");
  expect(designApi.quote).not.toHaveBeenCalled();
});

it("an idempotency conflict preserves a possibly executed request instead of allowing a new key",async()=>{
  const service=new LocalEditorOperation(target);
  await service.prepare(input(),source,references,signal());
  vi.mocked(designApi.submit).mockRejectedValueOnce(new DesignApiError("idempotency_conflict",409));
  await expect(service.submit(signal())).rejects.toThrow();
  expect(service.load()).toMatchObject({phase:"quoted",attempted:true});
  expect(service.discardQuote()).toBe(false);
});

it("unified references preserve interleaved order and edited usage while publishing each successful upload identity",async()=>{
  const service=new LocalEditorOperation(target),payload=input();
  payload.references=[
    {key:"new-a",file:new File(["a"],"a.png"),usage:"new color"},
    {key:"old",assetId:"old-ref",usage:"changed historical usage"},
    {key:"new-b",file:new File(["b"],"b.png"),usage:"shape"},
  ];
  const uploaded=vi.fn();
  await service.prepare(payload,source,[],signal(),uploaded);
  expect(vi.mocked(designApi.quote).mock.calls[0][1].attachments).toEqual([
    {asset_id:"asset-3",usage:"new color"},{asset_id:"old-ref",usage:"changed historical usage"},{asset_id:"asset-4",usage:"shape"}]);
  expect(uploaded.mock.calls.map(call=>call[0].key)).toEqual(["new-a","new-b"]);
});

it("a reference upload completing after cancellation cannot publish a callback or quote",async()=>{
 const abort=new AbortController(),uploaded=vi.fn();
 let calls=0;
 vi.mocked(designApi.upload).mockImplementation(async()=>{
  calls++;
  if(calls===3)abort.abort();
  return {id:"asset-"+calls,image_url:"",width:160,height:160,status:"ready"};
 });
 const service=new LocalEditorOperation(target);
 await expect(service.prepare(input(),source,references,abort.signal,uploaded)).rejects.toThrow();
 expect(uploaded).not.toHaveBeenCalled();
 expect(designApi.quote).not.toHaveBeenCalled();
 expect(service.load()).toBeNull();
});
