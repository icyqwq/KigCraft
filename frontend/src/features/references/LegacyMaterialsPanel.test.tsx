import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {act,cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import i18n from "../../i18n";
import Panel from "./LegacyMaterialsPanel";
let auth={currentUser:{id:"owner"},authSessionEpoch:0};
vi.mock("../auth/AuthProvider",()=>({useAuth:()=>auth}));
const item={id:"material",source_kind:"reference",file_name:"private original.png",legacy_kind:null,project_name:null,
 asset_id:"asset",width:1,height:1,draft_preserved:false,editor_document:false};
const clients:QueryClient[]=[];
function wrapper(){const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}});clients.push(client);
 return ({children}:{children:React.ReactNode})=><QueryClientProvider client={client}>{children}</QueryClientProvider>;}
beforeEach(async()=>{auth={currentUser:{id:"owner"},authSessionEpoch:0};await i18n.changeLanguage("en");});
afterEach(()=>{cleanup();clients.splice(0).forEach(c=>c.clear());vi.unstubAllGlobals();});
it("hides the previous owner's cached materials and makes no read for a mismatched owner",async()=>{
 const fetch=vi.fn().mockResolvedValue(Response.json({items:[item],next_cursor:null}));vi.stubGlobal("fetch",fetch);
 const {rerender}=render(<Panel ownerId="owner"/>,{wrapper:wrapper()});
 await screen.findByText(item.file_name);
 auth={currentUser:{id:"other"},authSessionEpoch:1};rerender(<Panel ownerId="owner"/>);
 expect(screen.queryByText(item.file_name)).not.toBeInTheDocument();expect(fetch).toHaveBeenCalledTimes(1);
});
it("does not reuse cached materials after the same owner starts a new auth session",async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(Response.json({items:[item],next_cursor:null})).mockResolvedValueOnce(Response.json({items:[],next_cursor:null}));
 vi.stubGlobal("fetch",fetch);
 const {rerender}=render(<Panel ownerId="owner"/>,{wrapper:wrapper()});await screen.findByText(item.file_name);
 auth={currentUser:{id:"owner"},authSessionEpoch:1};rerender(<Panel ownerId="owner"/>);
 expect(screen.queryByText(item.file_name)).not.toBeInTheDocument();
 await screen.findByText("No legacy materials imported yet.");expect(fetch).toHaveBeenCalledTimes(2);
});
it("rejects malformed data and retries without choosing or publishing anything",async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(Response.json({items:[{...item,editor_document:true}],next_cursor:null}))
 .mockResolvedValueOnce(Response.json({items:[item],next_cursor:null}));vi.stubGlobal("fetch",fetch);const choose=vi.fn();
 render(<Panel ownerId="owner" onChoose={choose} maximum={1}/>,{wrapper:wrapper()});
 await screen.findByRole("alert");expect(screen.queryByText(item.file_name)).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole("button",{name:"Try again"}));await screen.findByText(item.file_name);
 expect(choose).not.toHaveBeenCalled();expect(fetch.mock.calls[0][1].credentials).toBe("include");
});
it("aborts an outstanding private read when the panel closes",async()=>{
 let signal:AbortSignal|undefined;vi.stubGlobal("fetch",vi.fn((_url,init)=>{signal=init.signal;return new Promise(()=>{});}));
 const {unmount}=render(<Panel ownerId="owner"/>,{wrapper:wrapper()});
 await waitFor(()=>expect(signal).toBeDefined());act(()=>unmount());expect(signal!.aborted).toBe(true);
});
