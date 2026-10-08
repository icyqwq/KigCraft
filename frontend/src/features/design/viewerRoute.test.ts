import {expect,it} from "vitest";
import {readViewer,viewerSearch,viewerReturn,revisionKey} from "./viewerRoute";
it("round trips viewer choices while preserving scheme and search context",()=>{
 const search=new URLSearchParams("branch=b&message=m");
 const value={origin:"r1",selected:"r9",comparison:"r2",allVersions:true};
 const opened=viewerSearch(search,value);
 expect(readViewer(opened)).toEqual(value);
 expect(viewerSearch(opened,null).toString()).toBe(search.toString());
 expect(search.has("viewer")).toBe(false);
});
it("rejects malformed targets and collapses same-image comparison",()=>{
 for(const query of ["viewer=../../private","viewer=r1&image=","viewer=r1&compare=%3Cscript%3E","viewer=r1&versions=unknown"])
  expect(readViewer(new URLSearchParams(query))).toBeNull();
 expect(readViewer(new URLSearchParams("viewer=r1&compare=r1"))).toEqual({origin:"r1",selected:"r1",comparison:null,allVersions:false});
});
it("scopes return entries and revision caches to the account and design",()=>{
 const state={owner:"a",design:"d",key:"entry"};
 expect(viewerReturn(state,"a","d")).toEqual({key:"entry"});
 expect(viewerReturn(state,"b","d")).toBeNull();expect(viewerReturn(state,"a","other")).toBeNull();
 expect(viewerReturn({owner:"a",design:"d",key:1},"a","d")).toBeNull();
 expect(revisionKey("a","d","r")).not.toEqual(revisionKey("b","d","r"));
});
