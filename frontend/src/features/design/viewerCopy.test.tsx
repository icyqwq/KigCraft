import {act,cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import i18n from "../../i18n";
import en from "./viewer-copy-en";
import ja from "./viewer-copy-ja";
import zh from "./viewer-copy-zh-CN";
import errorsJa from "./errors-ja";
import RevisionViewer from "./RevisionViewer";
import RevisionViewerRoute from "./RevisionViewerRoute";
import {designApi,DesignApiError,type Revision} from "./api";
const clients:QueryClient[]=[];
afterEach(()=>{cleanup();for(const client of clients.splice(0))client.clear();vi.restoreAllMocks();});
function wrap(node:React.ReactNode){
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});clients.push(client);
 return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}
for(const [locale,copy] of Object.entries({en,ja}))it(locale+" preserves viewer resource keys and parameters",()=>{
 expect(Object.keys(copy).sort()).toEqual(Object.keys(zh).sort());
 const args=(text:string)=>[...text.matchAll(/\{\{(\w+)\}\}/g)].map(m=>m[1]).sort();
 for(const key of Object.keys(zh) as (keyof typeof zh)[]){
  expect(copy[key].trim(),key).not.toBe("");expect(args(copy[key]),key).toEqual(args(zh[key]));
 }
});
function revision(number:number):Revision{return {id:"r"+number,number,branch_id:"branch",parent_revision_id:null,
 asset_id:"a"+number,root_source_asset_id:"a"+number,root_image_url:"/root",image_url:"/image/"+number,
 operation_id:null,source_kind:"manual",view:"front",created_at:"",recipe:null,can_continue:true};}
it("keeps selected and comparison identities through language changes and only continues on explicit action",async()=>{
 const origin=revision(1),selected=revision(2),comparison=revision(3);
 const read=vi.spyOn(designApi,"candidates").mockResolvedValue({group:null,items:[origin,selected,comparison],total:3,next_cursor:null,watermark:3});
 const change=vi.fn(),continueFrom=vi.fn(),close=vi.fn();
 wrap(<RevisionViewer ownerId="owner" designId="design" revision={origin} selected={selected} comparison={comparison}
  allVersions={false} canContinue onChange={change} onContinue={continueFrom} onClose={close}/>);
 await screen.findByRole("button",{name:"查看候选：版本 3",exact:true});
 await act(async()=>{await i18n.changeLanguage("en");});
 expect(screen.getByRole("dialog")).toHaveAccessibleName("Version 2");
 expect(screen.getByRole("img",{name:"Compare: Version 3",exact:true})).toHaveAttribute("src",comparison.image_url);
 expect(screen.getByRole("link",{name:en.download,exact:true})).toHaveAttribute("href",selected.image_url);
 expect(screen.getByRole("combobox",{name:en.comparison})).toHaveValue(comparison.id);
 expect(read).toHaveBeenCalledTimes(1);expect(change).not.toHaveBeenCalled();expect(continueFrom).not.toHaveBeenCalled();expect(close).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:en.continue,exact:true}));
 expect(continueFrom).toHaveBeenCalledExactlyOnceWith(selected);
});
it("translates a failed deep link without refetching or exposing a partial image",async()=>{
 const origin=revision(1),read=vi.spyOn(designApi,"revision").mockRejectedValue(new DesignApiError("request_failed",503));
 const candidates=vi.spyOn(designApi,"candidates"),continueFrom=vi.fn(),change=vi.fn();
 wrap(<RevisionViewerRoute ownerId="owner" designId="design" location={{origin:origin.id,selected:"r2",comparison:null,allVersions:false}}
  known={[origin]} canContinue onChange={change} onContinue={continueFrom} onClose={()=>{}}/>);
 await screen.findAllByRole("alert");
 await act(async()=>{await i18n.changeLanguage("ja");});
 expect(screen.getByRole("dialog")).toHaveAccessibleName(ja.viewer);
 expect(screen.getAllByRole("alert").at(-1)!).toBeInTheDocument();
 expect(screen.queryByRole("link",{name:ja.download})).not.toBeInTheDocument();
 expect(screen.queryByRole("button",{name:ja.continue})).not.toBeInTheDocument();
 expect(read).toHaveBeenCalledTimes(1);expect(candidates).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:ja.retryImages,exact:true}));
 await waitFor(()=>expect(read).toHaveBeenCalledTimes(2));
 expect(read.mock.calls.map(args=>args.slice(0,2))).toEqual([["design","r2"],["design","r2"]]);
 expect(continueFrom).not.toHaveBeenCalled();expect(change).not.toHaveBeenCalled();
});

it("combines rapid controls with the latest selection before a parent render and guards continuation",async()=>{
 await act(async()=>{await i18n.changeLanguage("en");});
 const first=revision(1),second=revision(2),third={...revision(3),can_continue:false};
 vi.spyOn(designApi,"candidates").mockResolvedValue({group:null,items:[first,second,third],total:3,next_cursor:null,watermark:3});
 const change=vi.fn(),continueFrom=vi.fn();
 wrap(<RevisionViewer ownerId="owner" designId="design" revision={first} selected={first} comparison={third}
  allVersions={false} canContinue onChange={change} onContinue={continueFrom} onClose={()=>{}}/>);
 await screen.findByRole("button",{name:"View candidate: Version 2",exact:true});
 fireEvent.click(screen.getByRole("button",{name:"View candidate: Version 2",exact:true}));
 fireEvent.change(screen.getByRole("combobox",{name:en.comparison}),{target:{value:"r3"}});
 expect(change).toHaveBeenLastCalledWith(second,third,false);
 fireEvent.click(screen.getByRole("button",{name:en.continue,exact:true}));
 expect(continueFrom).toHaveBeenCalledExactlyOnceWith(second);
 fireEvent.click(screen.getByRole("button",{name:en.nextCandidate,exact:true}));
 expect(change).toHaveBeenLastCalledWith(third,second,false);
 fireEvent.click(screen.getByRole("button",{name:en.continue,exact:true}));
 expect(continueFrom).toHaveBeenCalledTimes(1);
 fireEvent.click(screen.getByRole("button",{name:en.versions,exact:true}));
 expect(change).toHaveBeenLastCalledWith(third,second,true);
});
