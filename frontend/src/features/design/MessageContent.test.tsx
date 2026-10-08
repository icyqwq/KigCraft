import {cleanup,render,screen,within} from "@testing-library/react";
import {afterEach,expect,it} from "vitest";
import {numberedAnnotations} from "./messageBindings";
import {MessageContent} from "./MessageContent";
afterEach(cleanup);
it("retains each historical attachment number when later messages remove or reuse an asset",()=>{
 render(<><article aria-label="older"><MessageContent parts={[{type:"references",items:[
  {asset_id:"a",usage:"color",number:1},{asset_id:"b",usage:"hair",number:2},{asset_id:"c",usage:"shape",number:3}]}]}/></article>
  <article aria-label="later"><MessageContent parts={[{type:"references",items:[{asset_id:"a",usage:"new",number:1},{asset_id:"c",usage:"shape",number:2}]}]}/></article></>);
 expect(within(screen.getByRole("article",{name:"older"})).getByRole("img",{name:"参考图 3"})).toHaveAttribute("src","/api/v2/assets/c");
 expect(within(screen.getByRole("article",{name:"later"})).getByRole("img",{name:"参考图 2"})).toHaveAttribute("src","/api/v2/assets/c");
});
it("preserves sparse frozen annotation numbers and separates visible text from instructions",()=>{
 render(<MessageContent parts={[{type:"annotations",annotations:[{annotation_id:"stable-c",number:3,note:"保留轮廓",text:"画面文字"}]}]}/>);
 const list=screen.getByRole("list",{name:"本轮标注"});
 expect(within(list).getByText("标注 3")).toBeVisible();
 expect(within(list).getByText("保留轮廓")).toBeVisible();
 expect(within(list).getByText("图中文字：画面文字")).toBeVisible();
 expect(within(list).getByRole("listitem")).toHaveAttribute("data-annotation-id","stable-c");
});
it("keeps blank annotations in the frozen numbering and preserves exact notes",()=>{
 const marks=[{id:"stable-a",note:"first"},{id:"blank-b",note:""},{id:"stable-c",note:" Keep rim "}];
 const bindings=numberedAnnotations(marks);marks.splice(1,1);
 expect(bindings.map(mark=>[mark.annotation_id,mark.number,mark.note])).toEqual([["stable-a",1,"first"],["blank-b",2,""],["stable-c",3," Keep rim "]]);
});
it("reads legacy reference order without requiring a current draft",()=>{
 render(<MessageContent parts={[{type:"references",items:[{asset_id:"b",usage:"B"},{asset_id:"a",usage:"A"}]}]}/>);
 expect(screen.getByRole("img",{name:"参考图 1"})).toHaveAttribute("src","/api/v2/assets/b");
});
