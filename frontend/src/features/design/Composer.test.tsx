import i18n from "../../i18n";
import en from "./copy-en";
import ja from "./copy-ja";
import errorsEn from "./errors-en";
import errorsJa from "./errors-ja";
import { act, cleanup, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Composer } from "./Composer";
import { designApi, DesignApiError, type Snapshot } from "./api";

const records=new Map<string,any>();
const snapshot: Snapshot = { id: "design", title: "Coral", last_branch_id: "branch", pinned: false,
  metadata_version: 0, updated_at: "", snapshot_sequence: 0,
  branch: { id: "branch", state_version: 2, current_revision_id: "revision" },
  messages: [], revisions: [], operations: [] };
beforeEach(() => {
  sessionStorage.clear();records.clear();
  vi.spyOn(designApi, "quote").mockResolvedValue({ id: "quote-1", max_units: 1, expires_at: "" });
  vi.spyOn(designApi, "submit").mockResolvedValue({ id: "operation", branch_id: "branch", kind: "image_edit",
    state: "queued", base_revision_id: "revision", reserved_units: 1, settled_units: 0, error_code: null });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
// There is no "saved" status text any more; wait until the draft is actually in storage.
const stored=(_text:string)=>Promise.resolve();
async function mount(extra = {}) {
  const onSubmitted = vi.fn().mockResolvedValue(undefined);
  const view = render(<Composer snapshot={snapshot} userId="user" canGenerate busy={false}
    requireAuth={action => { void action(); }} onSubmitted={onSubmitted} {...extra} />);
  await waitFor(()=>expect(screen.queryByText("\u6b63\u5728\u6062\u590d\u8349\u7a3f\u2026")).not.toBeInTheDocument());
  return { ...view, onSubmitted };
}
// The purpose of a reference is edited in a bubble opened from its thumbnail.
async function setUsage(number:number,value:string){
  fireEvent.click(screen.getByRole("button",{name:"参考图 "+number+" 用途"}));
  fireEvent.change(await screen.findByRole("textbox",{name:"参考图 "+number+" 用途"}),{target:{value}});
  fireEvent.click(screen.getByRole("button",{name:"完成"}));
  await waitFor(()=>expect(screen.queryByRole("textbox",{name:"参考图 "+number+" 用途"})).not.toBeInTheDocument());
}
// Sending is the consent: the composer quotes and submits straight away, with no confirmation dialog.
async function send() {
  fireEvent.change(screen.getByRole("textbox", { name: "设计要求" }), { target: { value: "只改成微笑" } });
  fireEvent.click(screen.getByRole("button", { name: "发送", exact: true }));
}
async function prepare() {
  await send();
  await waitFor(() => expect(designApi.submit).toHaveBeenCalled());
}
// Shown only when a submission could not be confirmed and must be retried or signed in again.
const pendingGroup = () => screen.findByRole("group", { name: "确认发送" });
it("quotes and submits on a single send without asking for confirmation", async () => {
  await mount();
  await prepare();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(designApi.quote).toHaveBeenCalledWith("branch", expect.objectContaining({
    text: "只改成微笑", base_revision_id: "revision", expected_branch_version: 2,
  }));
  expect(designApi.submit).toHaveBeenCalledTimes(1);
  expect(vi.mocked(designApi.submit).mock.calls[0][1]).toMatchObject({ quote_id: "quote-1" });
});
it("retries a lost response with the same request key and quote", async () => {
  await mount();
  vi.mocked(designApi.submit).mockRejectedValueOnce(new TypeError("network lost"));
  await send();
  const group = await pendingGroup();
  fireEvent.click(within(group).getByRole("button", { name: "重试发送", exact: true }));
  await waitFor(() => expect(designApi.submit).toHaveBeenCalledTimes(2));
  expect(vi.mocked(designApi.submit).mock.calls[0]).toEqual(vi.mocked(designApi.submit).mock.calls[1]);
  expect(designApi.quote).toHaveBeenCalledTimes(1);
});
it("keeps user text when login is requested", async () => {
  const requireAuth = vi.fn();
  await mount({ requireAuth, userId: undefined });
  fireEvent.change(screen.getByRole("textbox", { name: "设计要求" }), { target: { value: "珊瑚色假发" } });
  fireEvent.click(screen.getByRole("button", { name: "发送", exact: true }));
  expect(requireAuth).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("textbox", { name: "设计要求" })).toHaveValue("珊瑚色假发");
  expect(designApi.quote).not.toHaveBeenCalled();
});
it.each([
  {name:"active composition",isComposing:true,keyCode:13},
  {name:"composition-end Enter",isComposing:false,keyCode:229},
])("keeps $name for the IME and accepts the following normal Enter", async ({isComposing,keyCode}) => {
  await mount();
  const input = screen.getByRole("textbox", { name: "设计要求" });
  fireEvent.change(input, { target: { value: "珊瑚色短发" } });
  fireEvent.compositionStart(input);
  if(!isComposing)fireEvent.compositionEnd(input,{data:"珊瑚色短发"});
  const selecting=createEvent.keyDown(input,{key:"Enter",code:"Enter",keyCode,isComposing,bubbles:true,cancelable:true});
  await act(async()=>{fireEvent(input,selecting);});
  expect(selecting.defaultPrevented).toBe(false);
  expect(designApi.quote).not.toHaveBeenCalled();
  expect(designApi.submit).not.toHaveBeenCalled();
  expect(input).toHaveValue("珊瑚色短发");
  if(isComposing)fireEvent.compositionEnd(input,{data:"珊瑚色短发"});
  const newline=createEvent.keyDown(input,{key:"Enter",shiftKey:true,keyCode:13,bubbles:true,cancelable:true});
  await act(async()=>{fireEvent(input,newline);});
  expect(newline.defaultPrevented).toBe(false);
  expect(designApi.quote).not.toHaveBeenCalled();
  fireEvent.keyDown(input,{key:"Enter",keyCode:13,isComposing:false});
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(designApi.quote).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("keeps the request identity across an expired login", async () => {
  const onSignIn = vi.fn((action: () => Promise<void>) => { void action(); });
  await mount({ onSignIn });
  vi.mocked(designApi.submit).mockRejectedValueOnce(new DesignApiError("authentication_required", 401));
  await send();
  fireEvent.click(await within(await pendingGroup()).findByRole("button", { name: "登录后继续", exact: true }));
  await waitFor(() => expect(designApi.submit).toHaveBeenCalledTimes(2));
  expect(onSignIn).toHaveBeenCalledTimes(1);
  expect(vi.mocked(designApi.submit).mock.calls[0]).toEqual(vi.mocked(designApi.submit).mock.calls[1]);
});

const oldTarget = {id:"select-old",kind:"continue" as const,revisionId:"old-image",imageUrl:"/old.png",branchVersion:2};
it("pins a selected historical base and version when new results arrive", async()=>{
  const view=await mount({historyAction:oldTarget});
  view.rerender(<Composer snapshot={{...snapshot,branch:{...snapshot.branch,state_version:3,current_revision_id:"new-image"}}}
    userId="user" canGenerate busy={false} requireAuth={action=>{void action();}}
    historyAction={oldTarget} onSubmitted={view.onSubmitted}/>);
  await prepare();
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({
    base_revision_id:"old-image",expected_branch_version:2,continuation:{source_revision_id:"old-image",boundary_message_id:null}
  }));
});
it("cancelling a selected historical base returns to the current revision without submitting", async()=>{
  await mount({historyAction:oldTarget});
  fireEvent.click(await screen.findByRole("button",{name:"取消选择"}));
  expect(designApi.quote).not.toHaveBeenCalled();
  await prepare();
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({base_revision_id:"revision",continuation:null}));
});
it("regenerates the card's original request without asking again",async()=>{
  vi.mocked(designApi.quote).mockResolvedValue({id:"quote-old",max_units:1,expires_at:"",
    request_preview:{text:"原来的珊瑚色假发",base_image_url:"/original-base.png",references:[]}});
  await mount({historyAction:{...oldTarget,kind:"regenerate"}});
  expect(designApi.quote).not.toHaveBeenCalled();
  await waitFor(()=>expect(screen.queryByRole("textbox",{name:"设计要求"})).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({
    text:"",regenerate_revision_id:"old-image",base_revision_id:null,attachments:[],continuation:null
  }));
});
it("uses the returned lazy branch after a successful historical submission",async()=>{
  const view=await mount({historyAction:oldTarget});
  vi.mocked(designApi.submit).mockResolvedValue({id:"operation",branch_id:"new-branch",kind:"image_edit",
    state:"queued",base_revision_id:"old-image",reserved_units:1,settled_units:0,error_code:null});
  await prepare();
  await waitFor(()=>expect(view.onSubmitted).toHaveBeenCalledWith("design","new-branch"));
});
it("preserves successful reference uploads and retries a failed file without quoting or dispatching early",async()=>{
  URL.createObjectURL=vi.fn(()=>"blob:ref");URL.revokeObjectURL=vi.fn();
  const first=new File(["a"],"a.png",{type:"image/png"}),second=new File(["b"],"b.png",{type:"image/png"});
  const upload=vi.spyOn(designApi,"upload").mockResolvedValueOnce({id:"a",image_url:"",status:"ready",width:16,height:16})
    .mockRejectedValueOnce(new TypeError("second failed"))
    .mockResolvedValueOnce({id:"b",image_url:"",status:"ready",width:16,height:16});
  const view=await mount();
  fireEvent.change(view.container.querySelector('input[type="file"][multiple]')!,{target:{files:[first,second]}});
  await setUsage(1,"color");await setUsage(2,"shape");
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await screen.findByRole("button",{name:"重试上传参考图 2"});
  expect(designApi.quote).not.toHaveBeenCalled();expect(designApi.submit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"重试上传参考图 2"}));
  await waitFor(()=>expect(screen.queryByRole("button",{name:"重试上传参考图 2"})).not.toBeInTheDocument());
  expect(designApi.quote).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(upload.mock.calls.map(call=>call[0])).toEqual([first,second,second]);
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({attachments:[
    {asset_id:"a",usage:"color"},{asset_id:"b",usage:"shape"}]}));
});

it("reuses a historical reference in chat and lets its new usage differ from the old message",async()=>{
  const upload=vi.spyOn(designApi,"upload");
  await mount({snapshot:{...snapshot,messages:[{id:"m",role:"user",status:"completed",created_at:"",operation_id:null,
    content_parts:[{type:"references",items:[{asset_id:"existing",usage:"old usage"}]}]}]}});
  fireEvent.click(screen.getByRole("button",{name:"添加参考图"}));
  fireEvent.click(await screen.findByRole("menuitem",{name:"从本设计选择"}));
  const picker=await screen.findByRole("dialog",{name:"从本设计选择"});
  fireEvent.click(within(picker).getByRole("button",{name:"参考图 1",exact:true}));
  fireEvent.click(within(picker).getByRole("button",{name:"添加 (1)"}));
  await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await setUsage(1,"new usage");
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(upload).not.toHaveBeenCalled();
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({attachments:[{asset_id:"existing",usage:"new usage"}]}));
});

it("keeps next-round input editable while busy, without queuing a quote, then binds the current base on explicit send",async()=>{
  const view=await mount({busy:true});
  const input=screen.getByRole("textbox",{name:"设计要求"});
  expect(input).toBeEnabled();
  fireEvent.change(input,{target:{value:"下一轮 日本語 English"}});
  fireEvent.keyDown(input,{key:"Enter"});
  expect(screen.getByRole("button",{name:"发送",exact:true})).toBeDisabled();
  expect(designApi.quote).not.toHaveBeenCalled();
  view.rerender(<Composer snapshot={{...snapshot,branch:{...snapshot.branch,state_version:3,current_revision_id:"result"}}}
    userId="user" canGenerate busy={false} requireAuth={action=>{void action();}} onSubmitted={view.onSubmitted}/>);
  expect(input).toHaveValue("下一轮 日本語 English");
  expect(designApi.quote).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({text:"下一轮 日本語 English",base_revision_id:"result",expected_branch_version:3}));
});
it("does not clear an unknown receipt on idempotency conflict",async()=>{
  await mount();
  vi.mocked(designApi.submit).mockRejectedValueOnce(new DesignApiError("idempotency_conflict",409));
  await send();
  await within(await pendingGroup()).findByRole("button",{name:"重试发送",exact:true});
  expect(designApi.quote).toHaveBeenCalledTimes(1);
});
it("fences a late upload when the account changes",async()=>{
  URL.createObjectURL=vi.fn(()=>"blob:ref");URL.revokeObjectURL=vi.fn();
  let finish!:(asset:any)=>void;
  vi.spyOn(designApi,"upload").mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
  const view=await mount();
  fireEvent.change(view.container.querySelector('input[type="file"][multiple]')!,{target:{files:[new File(["x"],"x.png",{type:"image/png"})]}});
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await waitFor(()=>expect(designApi.upload).toHaveBeenCalledTimes(1));
  view.rerender(<Composer snapshot={snapshot} userId="other" canGenerate busy={false} requireAuth={action=>{void action();}} onSubmitted={view.onSubmitted}/>);
  await waitFor(()=>expect(screen.getByRole("textbox",{name:"设计要求"})).toBeEnabled());
  finish({id:"old-private",image_url:"",status:"ready",width:1,height:1});
  await waitFor(()=>expect(screen.queryByText("x.png")).not.toBeInTheDocument());
  expect(designApi.quote).not.toHaveBeenCalled();
  expect([...records.values()].filter(r=>r.scope.includes("other")).some(r=>JSON.stringify(r).includes("old-private"))).toBe(false);
});
it("still shows a localized generic error when a request rejects without an error value",async()=>{
  vi.mocked(designApi.submit).mockRejectedValueOnce(undefined);
  await mount();
  await send();
  await within(await pendingGroup()).findByRole("button",{name:"重试发送",exact:true});
  expect(screen.getAllByRole("alert").at(-1)!).toHaveTextContent("请求未完成，输入已保留。请重试。");
  expect(designApi.submit).toHaveBeenCalledTimes(1);
});


for(const transition of ["account","unmount"])for(const mode of ["batch","retry"])
it("cancels reference "+mode+" uploads on "+transition+" even when the response ignores abort",async()=>{
  URL.createObjectURL=vi.fn(()=>"blob:ref");URL.revokeObjectURL=vi.fn();
  const first=new File(["a"],"a.png",{type:"image/png"}),second=new File(["b"],"b.png",{type:"image/png"});
  const asset={id:"late-private",image_url:"",status:"ready",width:16,height:16};
  let release!:(value:typeof asset)=>void, count=0;
  const upload=vi.spyOn(designApi,"upload").mockImplementation(async()=>{
    count++;
    if(mode==="retry"&&count===1)throw new TypeError("initial upload failed");
    if(count===(mode==="batch"?1:2))return new Promise(resolve=>{release=resolve;});
    return {...asset,id:"unexpected-next"};
  });
  const view=await mount();
  fireEvent.change(view.container.querySelector('input[type="file"][multiple]')!,{target:{files:mode==="batch"?[first,second]:[first]}});
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  if(mode==="retry")fireEvent.click(await screen.findByRole("button",{name:"重试上传参考图 1"}));
  const expected=mode==="batch"?1:2;
  await waitFor(()=>expect(upload).toHaveBeenCalledTimes(expected));
  const signal=upload.mock.calls.at(-1)?.[2];
  if(transition==="account"){
    view.rerender(<Composer snapshot={snapshot} userId="other" canGenerate busy={false}
      requireAuth={action=>{void action();}} onSubmitted={view.onSubmitted}/>);
    await waitFor(()=>expect(screen.getByRole("textbox",{name:"设计要求"})).toBeEnabled());
  }else view.unmount();
  await act(async()=>{release(asset);});
  expect(upload).toHaveBeenCalledTimes(expected);
  expect(signal?.aborted).toBe(true);
  expect(designApi.quote).not.toHaveBeenCalled();
  expect(designApi.submit).not.toHaveBeenCalled();
  expect(view.onSubmitted).not.toHaveBeenCalled();
  expect([...records.values()].some(record=>record.content.attachments.some((item:{assetId?:string})=>item.assetId==="late-private"))).toBe(false);
});

it("shows a purpose dot for a reference without usage and no longer shows a permanent usage field",async()=>{
  URL.createObjectURL=vi.fn(()=>"blob:ref");URL.revokeObjectURL=vi.fn();
  const view=await mount();
  fireEvent.change(view.container.querySelector('input[type="file"][multiple]')!,{target:{files:[new File(["a"],"a.png",{type:"image/png"})]}});
  expect(screen.queryByRole("textbox",{name:"参考图 1 用途"})).not.toBeInTheDocument();
  expect(view.container.querySelector(".reference-thumb-dot")).not.toBeNull();
  await setUsage(1,"只参考发色");
  expect(view.container.querySelector(".reference-thumb-dot")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"移除参考图 1"}));
  expect(view.container.querySelector(".reference-thumb")).toBeNull();
});
it("limits the design picker to the remaining reference slots and ignores already attached images",async()=>{
  const items=[{asset_id:"r1",usage:"a"},{asset_id:"r2",usage:"b"},{asset_id:"r3",usage:"c"}];
  await mount({maxInputImages:3,snapshot:{...snapshot,messages:[{id:"m",role:"user",status:"completed",created_at:"",operation_id:null,
    content_parts:[{type:"references",items}]}]}});
  fireEvent.click(screen.getByRole("button",{name:"添加参考图"}));
  fireEvent.click(await screen.findByRole("menuitem",{name:"从本设计选择"}));
  const picker=await screen.findByRole("dialog",{name:"从本设计选择"});
  expect(within(picker).getByText("最多再添加 2 张")).toBeInTheDocument();
  for(const name of ["参考图 1","参考图 2","参考图 3"])fireEvent.click(within(picker).getByRole("button",{name,exact:true}));
  expect(within(picker).getByRole("button",{name:"添加 (2)"})).toBeEnabled();
  expect(within(picker).getByRole("button",{name:"参考图 3",exact:true})).toBeDisabled();
});
it("sends no base image when the user chooses 'no base image' from the base tag",async()=>{
  await mount({snapshot:{...snapshot,branch:{...snapshot.branch,current_revision_id:"head"},
    revisions:[{id:"head",number:2,image_url:"/head.png",can_continue:true},{id:"first",number:1,image_url:"/first.png",can_continue:true}] as any}});
  fireEvent.click(screen.getByRole("button",{name:"选择底图"}));
  fireEvent.click(await screen.findByRole("menuitem",{name:"不使用底图"}));
  fireEvent.change(screen.getByRole("textbox",{name:"设计要求"}),{target:{value:"从头来"}});
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({base_revision_id:null,continuation:null}));
});
it("continues from another version chosen in the base tag with the existing continuation semantics",async()=>{
  await mount({snapshot:{...snapshot,branch:{...snapshot.branch,current_revision_id:"head"},
    revisions:[{id:"head",number:2,image_url:"/head.png",can_continue:true},{id:"first",number:1,image_url:"/first.png",can_continue:true}] as any}});
  fireEvent.click(screen.getByRole("button",{name:"选择底图"}));
  fireEvent.click(await screen.findByRole("menuitem",{name:"v1"}));
  fireEvent.change(screen.getByRole("textbox",{name:"设计要求"}),{target:{value:"改旧图"}});
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({base_revision_id:"first",
    continuation:{source_revision_id:"first",boundary_message_id:null}}));
});
it("turns the send key into a stop key while a task runs and keeps the input editable",async()=>{
  const onStop=vi.fn();
  await mount({busy:true,onStop});
  expect(screen.getByRole("textbox",{name:"设计要求"})).toBeEnabled();
  expect(screen.queryByRole("button",{name:"发送",exact:true})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button",{name:"停止生成"}));
  expect(onStop).toHaveBeenCalledTimes(1);
});
it("accepts files dropped on the conversation through its handle",async()=>{
  URL.createObjectURL=vi.fn(()=>"blob:ref");URL.revokeObjectURL=vi.fn();
  const handle={current:null as null|{addFiles:(files:Iterable<File>)=>void}};
  const view=await mount({handle});
  act(()=>handle.current!.addFiles([new File(["a"],"a.png",{type:"image/png"})]));
  expect(view.container.querySelectorAll(".reference-thumb")).toHaveLength(1);
});
it("fills and sends the head-shell request once from a reference image through the normal quote and submit path",async()=>{
  const target={id:"from-reference",kind:"continue" as const,revisionId:"ref-rev",imageUrl:"/ref.png",branchVersion:2};
  const autoSend={id:"auto-1",text:"生成头壳效果图",target};
  const view=await mount({autoSend});
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(designApi.quote).toHaveBeenCalledTimes(1);
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({text:"生成头壳效果图",base_revision_id:"ref-rev",
    continuation:{source_revision_id:"ref-rev",boundary_message_id:null}}));
  // A re-render with the same request id must not send it a second time.
  view.rerender(<Composer snapshot={snapshot} userId="user" canGenerate busy={false} requireAuth={action=>{void action();}} onSubmitted={view.onSubmitted} autoSend={autoSend}/>);
  await new Promise(resolve=>setTimeout(resolve,50));
  expect(designApi.submit).toHaveBeenCalledTimes(1);
});

const newDesignMount=(extra={})=>{
  vi.spyOn(designApi,"create").mockResolvedValue({id:"design",last_branch_id:"branch",title:"x",pinned:false,metadata_version:0,updated_at:""} as any);
  return mount({snapshot:undefined,...extra});
};
it("sends the first-step choice with the first message of a new design",async()=>{
  await newDesignMount({targetKind:"reference"});
  fireEvent.change(screen.getByRole("textbox",{name:"设计要求"}),{target:{value:"珊瑚色狐娘"}});
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({text:"珊瑚色狐娘",target_kind:"reference",base_revision_id:null}));
  expect(vi.mocked(designApi.submit).mock.calls[0][1]).toEqual(expect.objectContaining({target_kind:"reference"}));
});
it("does not send a target kind for ordinary follow-up turns",async()=>{
  await mount({targetKind:"reference"});
  fireEvent.change(screen.getByRole("textbox",{name:"设计要求"}),{target:{value:"耳朵再大一点"}});
  fireEvent.click(screen.getByRole("button",{name:"发送",exact:true}));
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.not.objectContaining({target_kind:expect.anything()}));
});
it("asks for a head shell explicitly when making one from a reference image",async()=>{
  const target={id:"ref-target",kind:"continue" as const,revisionId:"ref-rev",imageUrl:"/ref.png",branchVersion:2};
  await mount({autoSend:{id:"auto-2",text:"生成头壳效果图",target}});
  await waitFor(()=>expect(designApi.submit).toHaveBeenCalledTimes(1));
  expect(designApi.quote).toHaveBeenCalledWith("branch",expect.objectContaining({target_kind:"head_shell",base_revision_id:"ref-rev"}));
});

