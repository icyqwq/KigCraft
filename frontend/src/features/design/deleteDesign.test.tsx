import {cleanup,fireEvent,render,screen,within} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {DesignActions} from "./DesignActions";
import {designApi,type Design} from "./api";
import zh from "./page-copy-zh-CN";
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});
const design:Design={id:"design",title:"Fixture",last_branch_id:"branch",pinned:false,metadata_version:1,updated_at:""};
it("asks for a permanent delete, says it cannot be undone, and uses the danger action",async()=>{
 render(<DesignActions design={design} onUpdated={async()=>{}} onRemoved={async()=>{}} onRename={()=>{}}/>);
 fireEvent.click(screen.getByRole("button",{name:zh.manage,exact:true}));
 fireEvent.click(screen.getByRole("menuitem",{name:zh.delete,exact:true}));
 const dialog=await screen.findByRole("dialog",{name:zh.deleteTitle,exact:true});
 expect(within(dialog).getByText(zh.deleteNote)).toBeVisible();
 expect(dialog.querySelector(".is-danger")).not.toBeNull();
 expect(document.body.textContent).not.toMatch(/回收站|恢复设计/);
});
it("sends only the metadata version as the delete confirmation",async()=>{
 const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({deleted:true})});
 vi.stubGlobal("fetch",fetch);
 await designApi.remove(design);
 expect(fetch.mock.calls[0][0]).toBe("/api/v2/designs/design");
 expect(fetch.mock.calls[0][1].method).toBe("DELETE");
 expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({expected_metadata_version:1});
});
