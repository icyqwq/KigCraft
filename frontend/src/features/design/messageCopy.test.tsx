import {act,cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {afterEach,expect,it,vi} from "vitest";
import i18n from "../../i18n";
import {designApi,DesignApiError,type Snapshot} from "./api";
import {MessageContent} from "./MessageContent";
import InlineQuestion from "./InlineQuestion";
import en from "./message-copy-en";
import ja from "./message-copy-ja";
import zh from "./message-copy-zh-CN";
afterEach(()=>{cleanup();vi.restoreAllMocks();});
for(const [locale,copy] of Object.entries({en,ja}))it(locale+" preserves message resource keys and interpolation",()=>{
 expect(Object.keys(copy).sort()).toEqual(Object.keys(zh).sort());
 const args=(s:string)=>[...s.matchAll(/\{\{(\w+)\}\}/g)].map(m=>m[1]).sort();
 for(const key of Object.keys(zh) as (keyof typeof zh)[])expect(args(copy[key])).toEqual(args(zh[key]));
});
it("changes only system labels while preserving frozen sparse numbers, user text and annotation identity",async()=>{
 const parts=[{type:"text",text:"回复已中断 {{text}} <b>literal</b>"},
  {type:"references",items:[{asset_id:"asset-c",usage:"参考图 3 {{number}}",number:7}]},
  {type:"annotations",annotations:[{annotation_id:"stable",number:9,text:"{{text}} <b>原文</b>",note:"  保留原文  "}]}];
 const before=JSON.stringify(parts);render(<MessageContent parts={parts}/>);
 for(const locale of ["en","ja"]){
  await act(async()=>{await i18n.changeLanguage(locale);});
  expect(screen.getByRole("img")).toHaveAccessibleName(locale==="en"?"Reference 7":"参考画像 7");
  expect(screen.getByRole("listitem")).toHaveAttribute("value","9");
  expect(screen.getByRole("listitem")).toHaveAttribute("data-annotation-id","stable");
  expect(screen.getByText("{{text}} <b>原文</b>",{exact:false})).toBeVisible();
  expect(screen.getByText("回复已中断 {{text}} <b>literal</b>",{exact:true})).toBeVisible();
  expect(document.querySelector("b")).toBeNull();expect(JSON.stringify(parts)).toBe(before);
 }
});
const snapshot:Snapshot={id:"design",title:"Title",last_branch_id:"branch",pinned:false,metadata_version:0,updated_at:"",snapshot_sequence:0,
 branch:{id:"branch",state_version:0,current_revision_id:null},messages:[],revisions:[],operations:[]};
it("localizes a question read error and resumes the same query only after explicit sign-in",async()=>{
 const prepare=vi.spyOn(designApi,"prepareQuestion").mockRejectedValue(new DesignApiError("unauthenticated",401));
 const signIn=vi.fn(),client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 render(<QueryClientProvider client={client}><InlineQuestion ownerId="owner" snapshot={snapshot} operationId="original"
  maxInputImages={8} canGenerate canLocalGenerate busy={false} onSubmitted={async()=>{}} onSignIn={signIn}/></QueryClientProvider>);
 await screen.findByRole("button",{name:"登录后继续",exact:true});
 await act(async()=>{await i18n.changeLanguage("en");});
 expect(screen.getByRole("group")).toHaveAccessibleName(en.answer);
 expect(prepare).toHaveBeenCalledTimes(1);expect(signIn).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:"登录后继续",exact:true}));
 expect(signIn).toHaveBeenCalledTimes(1);expect(prepare).toHaveBeenCalledTimes(1);
 await act(async()=>{await i18n.changeLanguage("ja");});
 await act(async()=>{await signIn.mock.calls[0][0]();});
 await waitFor(()=>expect(prepare).toHaveBeenCalledTimes(2));
 expect(prepare.mock.calls.map(args=>args[0])).toEqual(["original","original"]);client.clear();
});

it("language changes neither advance stream progress nor consume unread content",async()=>{
 const {StreamingMessage,messageStreamKey}=await import("./StreamingMessage");
 const {NewContentButton}=await import("./NewContentButton");
 const {unreadSignal}=await import("./conversationPosition");
 const client=new QueryClient(),key=messageStreamKey("owner","design","message");
 const cached={sequence:2,branch_id:"branch",operation_id:"op",text:"原文 {{text}}"};
 client.setQueryData(key,cached);
 const progress=vi.fn(),clicked=vi.fn(),signal=unreadSignal();signal.set(true);
 const element=(status="streaming")=><QueryClientProvider client={client}>
  <StreamingMessage ownerId="owner" designId="design" snapshotSequence={1} onProgress={progress}
   message={{id:"message",branch_id:"branch",role:"assistant",status,created_at:"",operation_id:"op",content_parts:[{type:"text",text:"原文 {{text}}"}]}}/>
  <NewContentButton signal={signal} onClick={()=>{clicked();signal.set(false);}}/>
 </QueryClientProvider>;
 const view=render(element());expect(progress).toHaveBeenCalledTimes(1);
 for(const locale of ["en","ja"]){
  await act(async()=>{await i18n.changeLanguage(locale);});
  expect(screen.getByText("原文 {{text}}",{exact:true})).toBeVisible();
  expect(signal.get()).toBe(true);expect(clicked).not.toHaveBeenCalled();
  expect(progress).toHaveBeenCalledTimes(1);expect(client.getQueryData(key)).toEqual(cached);
 }
 view.rerender(element("interrupted"));expect(screen.getByText(ja.interrupted)).toBeVisible();
 fireEvent.click(screen.getByRole("button",{name:ja.newContent}));
 expect(signal.get()).toBe(false);expect(clicked).toHaveBeenCalledTimes(1);client.clear();
});
