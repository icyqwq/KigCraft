import {act,cleanup,render,screen} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import i18n from "../../i18n";
import en from "./editor-copy-en";
import ja from "./editor-copy-ja";
import zh from "./editor-copy-zh-CN";
import EditorDialog from "./EditorDialog";
import {designApi,type Revision} from "./api";
import {ManualEditorSave} from "./manualSave";
import {LocalEditorOperation} from "./localOperation";
const clients:QueryClient[]=[];
afterEach(()=>{cleanup();for(const client of clients.splice(0))client.clear();sessionStorage.clear();vi.restoreAllMocks();});
for(const [locale,copy] of Object.entries({en,ja}))it(locale+" preserves cloud editor resource keys and parameters",()=>{
 expect(Object.keys(copy).sort()).toEqual(Object.keys(zh).sort());
 const args=(text:string)=>[...text.matchAll(/\{\{(\w+)\}\}/g)].map(m=>m[1]).sort();
 for(const key of Object.keys(zh) as (keyof typeof zh)[]){
  expect(copy[key].trim(),key).not.toBe("");expect(args(copy[key]),key).toEqual(args(zh[key]));
 }
});
for(const kind of ["manual","local"] as const)it(kind+" recovery failure stays visible without rewriting records or retrying on language change",async()=>{
 const target={ownerId:"owner",branchId:"branch",baseRevisionId:"base",rootAssetId:"root",branchVersion:3,historical:false};
 const service=kind==="manual"?new ManualEditorSave(target):new LocalEditorOperation(target);
 const record=JSON.stringify({schema:99,ownerId:"owner",keep:"{{number}} <b>record</b>"});
 sessionStorage.setItem(service.key,record);
 const manualLoad=vi.spyOn(ManualEditorSave.prototype,"load"),localLoad=vi.spyOn(LocalEditorOperation.prototype,"load");
 const read=vi.spyOn(designApi,"editorCheckpoint").mockRejectedValue(undefined);
 const save=vi.spyOn(designApi,"saveEditor"),signIn=vi.fn(),close=vi.fn();
 const revision:Revision={id:"base",number:42,branch_id:"branch",parent_revision_id:null,asset_id:"root",root_source_asset_id:"root",
  root_image_url:"/root",image_url:"/image",operation_id:null,source_kind:"manual",view:"front",created_at:"",recipe:null};
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});clients.push(client);
 render(<QueryClientProvider client={client}><EditorDialog ownerId="owner" designId="design" revision={revision}
  branchId="branch" branchVersion={3} historical={false} onClose={close} onSaved={async()=>{}} onSubmitted={async()=>{}} onSignIn={signIn}/></QueryClientProvider>);
 const key=kind==="manual"?"manualRecoveryFailed":"localRecoveryFailed";
 await screen.findByText(zh[key],{exact:true});await screen.findByText(zh.checkpointUnavailable,{exact:true});
 for(const [locale,copy] of [["en",en],["ja",ja]] as const){
  await act(async()=>{await i18n.changeLanguage(locale);});
  expect(screen.getByText(copy[key],{exact:true})).toBeVisible();
  expect(screen.getByText(copy.checkpointUnavailable,{exact:true})).toBeVisible();
  expect(sessionStorage.getItem(service.key)).toBe(record);
  expect(read).toHaveBeenCalledTimes(1);expect(manualLoad).toHaveBeenCalledTimes(1);expect(localLoad).toHaveBeenCalledTimes(1);
  expect(save).not.toHaveBeenCalled();expect(signIn).not.toHaveBeenCalled();expect(close).not.toHaveBeenCalled();
 }
});
