import {act,cleanup,fireEvent,render,renderHook,screen} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {MemoryRouter} from "react-router-dom";
import i18n from "../../i18n";
import zh from "./copy-zh-CN";
import en from "./copy-en";
import ja from "./copy-ja";
import legacyZh from "../design/legacy-copy-zh-CN";
import legacyEn from "../design/legacy-copy-en";
import legacyJa from "../design/legacy-copy-ja";
import {useRecoveryCopy} from "./useRecoveryCopy";
import {useLegacyCopy} from "../design/useLegacyCopy";
import {adminRecovery,RecoveryError,type Command} from "./api";
import AdminRecoveryPage from "./AdminRecoveryPage";
vi.mock("../auth/AuthProvider",()=>({useAuth:()=>({currentUser:{id:"admin",role:"admin"},authSessionEpoch:1})}));
const clients:QueryClient[]=[];
afterEach(()=>{cleanup();for(const client of clients.splice(0))client.clear();sessionStorage.clear();vi.restoreAllMocks();});
for(const [name,base,copy] of [["recovery/en",zh,en],["recovery/ja",zh,ja],["legacy/en",legacyZh,legacyEn],["legacy/ja",legacyZh,legacyJa]] as const){
 it(name+" preserves keys and interpolation parameters",()=>{
  expect(Object.keys(copy).sort()).toEqual(Object.keys(base).sort());
  const args=(s:string)=>[...s.matchAll(/\{\{(\w+)\}\}/g)].map(m=>m[1]).sort();
  for(const key of Object.keys(base)){
   const original=(base as Record<string,string>)[key],translated=(copy as Record<string,string>)[key];
   expect(translated.trim(),key).not.toBe("");expect(args(translated),key).toEqual(args(original));
  }
 });
}
it("recovery errors and automatic states use current language with safe unknown fallbacks",async()=>{
 const {result}=renderHook(()=>useRecoveryCopy());
 const original=new RecoveryError("refund_exceeds_settlement",409);
 for(const [locale,copy] of [["en",en],["ja",ja]] as const){
  await act(async()=>{await i18n.changeLanguage(locale);});
  expect(result.current.message(original)).toBe(copy.error_refund_exceeds_settlement);
  expect(result.current.message(new RecoveryError("__proto__",500))).toBe(copy.requestUnknown);
  expect(result.current.message("recovery_storage_failed")).toBe(copy.storageFailure);
  expect(result.current.automatic("__proto__")).toBe(copy.autoUnknown);
  expect(result.current.label("__proto__")).toBe("__proto__");
  expect(result.current.confirm("refund")).toBe(copy.confirm_refund);
 }
});
it("legacy fallbacks translate without interpreting provider text",async()=>{
 const {result}=renderHook(()=>useLegacyCopy());
 const provider="{{provider}} <b>original</b>";
 for(const [locale,copy] of [["en",legacyEn],["ja",legacyJa]] as const){
  await act(async()=>{await i18n.changeLanguage(locale);});
  expect(result.current.c("imageSource",{provider})).toBe(copy.imageSource.replace("{{provider}}",provider));
  expect(result.current.view("__proto__")).toBe(copy.view_unknown);
  expect(result.current.state("__proto__")).toBe(copy.stateUnconfirmed);
 }
});
it("unknown admin submission retains the original command across language changes and translates its receipt",async()=>{
 const overview=vi.spyOn(adminRecovery,"overview").mockResolvedValue({controls:{},operation_counts:{},recovery_requests:0,recovery_completed:0,duplicate_requests:0,
  upstream_cost:{status:"unknown",amount:null},platform_capacity:{status:"unknown"}});
 const operations=vi.spyOn(adminRecovery,"operations").mockResolvedValue({items:[],has_more:false});
 const actions=vi.spyOn(adminRecovery,"actions").mockResolvedValue({items:[],has_more:false});
 const submit=vi.spyOn(adminRecovery,"submit").mockRejectedValueOnce(undefined).mockImplementationOnce(async(request:Command)=>({
  ...request,id:"receipt",actor_id:"admin",units:request.units??null,status:"completed",result:{},error_code:null,replay_count:1,created_at:"",started_at:null,completed_at:null,
 }));
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});clients.push(client);
 render(<MemoryRouter><QueryClientProvider client={client}><AdminRecoveryPage/></QueryClientProvider></MemoryRouter>);
 await screen.findByText(zh.allowed);
 fireEvent.click(screen.getByRole("button",{name:zh.label_pause,exact:true}));
 const reason="Keep {{raw}} <unchanged>";
 fireEvent.change(screen.getByLabelText(zh.reason),{target:{value:reason}});
 fireEvent.click(screen.getByRole("button",{name:zh.confirm_pause,exact:true}));
 await screen.findByText(zh.requestUnknown);
 const original=submit.mock.calls[0][0],record=sessionStorage.getItem("kig-v2-admin-request:admin");
 expect(original.reason).toBe(reason);
 for(const [locale,copy] of [["en",en],["ja",ja]] as const){
  await act(async()=>{await i18n.changeLanguage(locale);});
  expect(screen.getByRole("alert")).toHaveTextContent(copy.requestUnknown);
  expect(screen.getByRole("region",{name:copy.unconfirmed})).toHaveTextContent(reason);
  expect(sessionStorage.getItem("kig-v2-admin-request:admin")).toBe(record);
  expect(submit).toHaveBeenCalledTimes(1);
  expect(overview).toHaveBeenCalledTimes(1);expect(operations).toHaveBeenCalledTimes(1);expect(actions).toHaveBeenCalledTimes(1);
 }
 fireEvent.click(screen.getByRole("button",{name:ja.retryOriginal,exact:true}));
 await screen.findByText(ja.label_completed+" · receipt");
 expect(submit.mock.calls.map(call=>call[0])).toEqual([original,original]);
 expect(sessionStorage.getItem("kig-v2-admin-request:admin")).toBeNull();
 await act(async()=>{await i18n.changeLanguage("en");});
 expect(screen.getByRole("status")).toHaveTextContent(en.label_completed+" · receipt");
 expect(submit).toHaveBeenCalledTimes(2);
});
