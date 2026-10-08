import {act,cleanup,fireEvent,render,screen,within,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import i18n from "../../i18n";
import en from "./page-copy-en";
import ja from "./page-copy-ja";
import zh from "./page-copy-zh-CN";
import errorsEn from "./errors-en";
import errorsJa from "./errors-ja";
import {DesignActions} from "./DesignActions";
import {designApi,DesignApiError,type Design,type Operation} from "./api";
afterEach(()=>{cleanup();vi.restoreAllMocks();});
for(const [locale,copy] of Object.entries({en,ja}))it(locale+" preserves design resource keys and parameters",()=>{
 expect(Object.keys(copy).sort()).toEqual(Object.keys(zh).sort());
 const args=(text:string)=>[...text.matchAll(/\{\{(\w+)\}\}/g)].map(m=>m[1]).sort();
 for(const key of Object.keys(zh) as (keyof typeof zh)[]){
  expect(copy[key].trim(),key).not.toBe("");expect(args(copy[key]),key).toEqual(args(zh[key]));
 }
});
const design:Design={id:"design",title:"管理设计 {{date}} <b>name</b>",last_branch_id:"branch",pinned:false,metadata_version:4,updated_at:""};
it("keeps a failed delete confirmation and its metadata version while translating the error",async()=>{
 const remove=vi.spyOn(designApi,"remove").mockRejectedValue(new DesignApiError("metadata_conflict",409));
 const read=vi.spyOn(designApi,"lifecycle").mockRejectedValue(new Error("offline"));
 const updated=vi.fn().mockResolvedValue(undefined),removed=vi.fn();
 render(<DesignActions design={design} onUpdated={updated} onRemoved={removed} onRename={()=>{}}/>);
 fireEvent.click(screen.getByRole("button",{name:zh.manage,exact:true}));
 fireEvent.click(screen.getByRole("menuitem",{name:zh.delete,exact:true}));
 const dialog=await screen.findByRole("dialog",{name:zh.deleteTitle,exact:true});
 fireEvent.click(within(dialog).getByRole("button",{name:zh.delete,exact:true}));
 await screen.findAllByRole("alert");
 for(const [locale,copy,errors] of [["en",en,errorsEn],["ja",ja,errorsJa]] as const){
  await act(async()=>{await i18n.changeLanguage(locale);});
  expect(dialog).toHaveAccessibleName(copy.deleteTitle);
  expect(within(dialog).getByText(design.title,{exact:true})).toBeVisible();
  expect(screen.getAllByRole("alert").at(-1)!).toBeInTheDocument();
  expect(remove).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1);
 }
 expect(remove).toHaveBeenCalledWith(design);expect(updated).toHaveBeenCalledTimes(1);expect(removed).not.toHaveBeenCalled();
});
it("keeps cancellation and delete restrictions after changing language",async()=>{
 const cancel=vi.spyOn(designApi,"cancel"),remove=vi.spyOn(designApi,"remove");
 const operation:Operation={id:"op",branch_id:"branch",kind:"generate",state:"cancelling",base_revision_id:null,reserved_units:1,settled_units:0,error_code:null};
 render(<DesignActions design={design} operation={operation} onUpdated={async()=>{}} onRemoved={async()=>{}} onRename={()=>{}}/>);
 fireEvent.click(screen.getByRole("button",{name:zh.manage,exact:true}));
 fireEvent.click(screen.getByRole("menuitem",{name:zh.delete,exact:true}));
 await screen.findByRole("dialog");
 await act(async()=>{await i18n.changeLanguage("en");});
 expect(screen.getByRole("status")).toHaveTextContent(en.stoppingBeforeDelete);
 expect(screen.getByRole("button",{name:en.stopTask,exact:true})).toBeDisabled();
 expect(screen.queryByRole("button",{name:en.delete,exact:true})).not.toBeInTheDocument();
 expect(cancel).not.toHaveBeenCalled();expect(remove).not.toHaveBeenCalled();
});
it("treats a vanished design as deleted when the delete reply is lost, and never resends it",async()=>{
 const remove=vi.spyOn(designApi,"remove").mockRejectedValue(new TypeError("network"));
 const read=vi.spyOn(designApi,"lifecycle").mockRejectedValue(new DesignApiError("not_found",404));
 const updated=vi.fn().mockResolvedValue(undefined),removed=vi.fn().mockResolvedValue(undefined);
 render(<DesignActions design={design} onUpdated={updated} onRemoved={removed} onRename={()=>{}}/>);
 fireEvent.click(screen.getByRole("button",{name:zh.manage,exact:true}));
 fireEvent.click(screen.getByRole("menuitem",{name:zh.delete,exact:true}));
 const dialog=await screen.findByRole("dialog",{name:zh.deleteTitle,exact:true});
 fireEvent.click(within(dialog).getByRole("button",{name:zh.delete,exact:true}));
 await waitFor(()=>expect(removed).toHaveBeenCalledTimes(1));
 expect(remove).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledTimes(1);expect(updated).not.toHaveBeenCalled();
});

it("shows one of the kigurumi starter lines in the empty conversation, in every language", async () => {
  const { EmptyConversation, STARTER_LINES } = await import("./EmptyStates");
  const { render, screen, cleanup } = await import("@testing-library/react");
  const { MemoryRouter } = await import("react-router-dom");
  const zh = (await import("./page-copy-zh-CN")).default as Record<string, string>;
  const en = (await import("./page-copy-en")).default as Record<string, string>;
  const ja = (await import("./page-copy-ja")).default as Record<string, string>;
  for (const key of STARTER_LINES) expect([zh[key], en[key], ja[key]].every(Boolean)).toBe(true);
  const random = vi.spyOn(Math, "random").mockReturnValue(0.99);
  render(<MemoryRouter><EmptyConversation /></MemoryRouter>);
  expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(zh.startIdea9);
  random.mockRestore(); cleanup();
});
