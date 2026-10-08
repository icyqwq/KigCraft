import {act,cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import i18n from "../../i18n";
import en from "./viewset-copy-en";
import ja from "./viewset-copy-ja";
import zh from "./viewset-copy-zh-CN";
import errorsJa from "./errors-ja";
import FourViewDialog from "./FourViewDialog";
import {designApi,type Revision} from "./api";
const clients:QueryClient[]=[];
afterEach(()=>{cleanup();for(const client of clients.splice(0))client.clear();sessionStorage.clear();vi.restoreAllMocks();});
for(const [locale,copy] of Object.entries({en,ja}))it(locale+" preserves viewset resource keys and parameters",()=>{
 expect(Object.keys(copy).sort()).toEqual(Object.keys(zh).sort());
 const args=(text:string)=>[...text.matchAll(/\{\{(\w+)\}\}/g)].map(m=>m[1]).sort();
 for(const key of Object.keys(zh) as (keyof typeof zh)[]){
  expect(copy[key].trim(),key).not.toBe("");expect(args(copy[key]),key).toEqual(args(zh[key]));
 }
});
const revision:Revision={id:"r1",number:2,branch_id:"main",parent_revision_id:null,asset_id:"a1",root_source_asset_id:"a1",
 root_image_url:"/root",image_url:"/image",operation_id:null,source_kind:"manual",view:"front",created_at:"",recipe:null};
function mount(onSubmit=vi.fn(),extra:Partial<{canGenerate:boolean;busy:boolean}>={}){
 vi.spyOn(designApi,"revision").mockResolvedValue(revision);
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});clients.push(client);
 render(<QueryClientProvider client={client}><FourViewDialog owner="owner" design="design" source="r1" canGenerate busy={false} onClose={()=>{}} onSubmit={onSubmit} {...extra}/></QueryClientProvider>);
 return onSubmit;
}
it("asks only for extra pictures and a note, with no caption under the source and no side or feature fields",async()=>{
 mount();await screen.findByRole("textbox",{name:zh.notes});
 expect(screen.queryByText(/不计费/)).not.toBeInTheDocument();
 expect(screen.queryByRole("radio")).not.toBeInTheDocument();expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});
it("hands the note, the local pictures and the chosen front to the conversation instead of sending anything itself",async()=>{
 const onSubmit=mount();await screen.findByRole("textbox",{name:zh.notes});
 URL.createObjectURL=vi.fn(()=>"blob:ref");URL.revokeObjectURL=vi.fn();
 const upload=vi.spyOn(designApi,"upload");
 const file=new File(["b"],"back.png",{type:"image/png"});
 fireEvent.change(document.querySelector('input[type="file"][multiple]')!,{target:{files:[file]}});
 fireEvent.change(screen.getByRole("textbox",{name:zh.notes}),{target:{value:"  背面是长直发  "}});
 fireEvent.click(screen.getByRole("button",{name:zh.viewQuote,exact:true}));
 expect(onSubmit).toHaveBeenCalledTimes(1);
 expect(onSubmit.mock.calls[0][0]).toMatchObject({revision:{id:"r1"},text:"背面是长直发",attachments:[{file}]});
 expect(upload).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:zh.viewQuote,exact:true}));
 expect(onSubmit).toHaveBeenCalledTimes(1);
});
it("keeps the typed note when changing language",async()=>{
 mount();await screen.findByRole("textbox",{name:zh.notes});
 fireEvent.change(screen.getByRole("textbox",{name:zh.notes}),{target:{value:"背面 {{number}} <b>literal</b>"}});
 await act(async()=>{await i18n.changeLanguage("en");});
 expect(screen.getByRole("textbox",{name:en.notes})).toHaveValue("背面 {{number}} <b>literal</b>");
});
it("cannot generate while another operation runs",async()=>{
 const onSubmit=mount(vi.fn(),{busy:true});await screen.findByRole("textbox",{name:zh.notes});
 expect(screen.getByRole("button",{name:zh.viewQuote,exact:true})).toBeDisabled();expect(onSubmit).not.toHaveBeenCalled();
});
