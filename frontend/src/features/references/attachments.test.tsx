import i18n from "../../i18n";
import {createRef, useState} from "react";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {act,cleanup,fireEvent,render,screen} from "@testing-library/react";
import {ReferenceAttachments,type ReferenceAttachmentsHandle} from "./ReferenceAttachments";
import {appendReferenceFiles,ReferenceUploader,type ReferenceAttachment} from "./attachments";
beforeEach(()=>{URL.createObjectURL=vi.fn(()=>"blob:preview");URL.revokeObjectURL=vi.fn();});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
it("keeps selected assets and multiple uploaded files with independent usage and stable replacement identity",()=>{
  let current:ReferenceAttachment[]=[];
  function Harness(){
    const [items,setItems]=useState<ReferenceAttachment[]>([]);
    current=items;
    return <ReferenceAttachments items={items} onChange={setItems} maximum={4}
      options={[{key:"history",label:"历史图",description:"饰品"}]} fileInputTestId="files"/>;
  }
  render(<Harness/>);
  fireEvent.click(screen.getByRole("button",{name:"历史图"}));
  const a=new File(["a"],"a.png",{type:"image/png"}),b=new File(["b"],"b.png",{type:"image/png"});
  fireEvent.change(screen.getByTestId("files"),{target:{files:[a,b]}});
  expect(current.map(item=>item.assetId??item.file?.name)).toEqual(["history","a.png","b.png"]);
  fireEvent.change(screen.getByRole("textbox",{name:"参考图 2 用途"}),{target:{value:"只参考颜色"}});
  const original=current[1].key;
  fireEvent.click(screen.getByRole("button",{name:"替换参考图 2"}));
  const replacement=new File(["r"],"r.png",{type:"image/png"});
  fireEvent.change(screen.getByLabelText("替换参考图文件"),{target:{files:[replacement]}});
  expect(current[1]).toEqual({key:original,usage:"只参考颜色",file:replacement});
  fireEvent.click(screen.getByRole("button",{name:"移除参考图 1"}));
  expect(current.map(item=>item.file?.name)).toEqual(["r.png","b.png"]);
  expect(screen.getByRole("textbox",{name:"参考图 1 用途"})).toHaveValue("只参考颜色");
});
it("rejects the entire invalid or over-limit batch and preserves existing choices",()=>{
  const initial=[{key:"one",assetId:"a",usage:"existing"}];
  const file=new File(["a"],"a.png",{type:"image/png"});
  expect(()=>appendReferenceFiles(initial,[file,file],2)).toThrow("最多选择");
  expect(()=>appendReferenceFiles(initial,[file,new File(["x"],"bad.txt",{type:"text/plain"})],3)).toThrow("bad.txt");
  expect(initial).toEqual([{key:"one",assetId:"a",usage:"existing"}]);
});
it("uses the same upload path for drop and paste without bubbling files to an outer composer",()=>{
  let current:ReferenceAttachment[]=[];
  const outer=vi.fn();
  function Harness(){const [items,setItems]=useState(current);current=items;
    return <div onDrop={outer}><ReferenceAttachments items={items} onChange={setItems} maximum={3}/></div>;}
  render(<Harness/>);
  const area=screen.getByRole("region",{name:"参考图"});
  fireEvent.drop(area,{dataTransfer:{files:[new File(["a"],"a.png",{type:"image/png"})]}});
  fireEvent.paste(area,{clipboardData:{files:[new File(["b"],"b.png",{type:"image/png"})]}});
  expect(current.map(item=>item.file?.name)).toEqual(["a.png","b.png"]);
  expect(outer).not.toHaveBeenCalled();
});
it("retains successful uploads when a later file fails and retries only missing files in order",async()=>{
  const a=new File(["a"],"a.png"),b=new File(["b"],"b.png"),c=new File(["c"],"c.png");
  const items=[{key:"a",file:a,usage:"A"},{key:"history",assetId:"old",usage:"H"},{key:"b",file:b,usage:"B"},{key:"c",file:c,usage:"C"}];
  const uploader=new ReferenceUploader(),saved:ReferenceAttachment[]=[];
  const upload=vi.fn().mockResolvedValueOnce({id:"asset-a"}).mockRejectedValueOnce(new Error("file b failed"))
    .mockResolvedValueOnce({id:"asset-b"}).mockResolvedValueOnce({id:"asset-c"});
  await expect(uploader.prepare(items,upload,item=>saved.push(item))).rejects.toThrow("b.png");
  expect(saved.map(item=>item.assetId)).toEqual(["asset-a"]);
  const ready=await uploader.prepare(items,upload);
  expect(upload.mock.calls.map(call=>call[0])).toEqual([a,b,b,c]);
  expect(ready.map(item=>[item.assetId,item.usage])).toEqual([["asset-a","A"],["old","H"],["asset-b","B"],["asset-c","C"]]);
  expect(items[0].assetId).toBeUndefined();
});

it("updates validation language without changing files, literal names, usage, or custom labels",async()=>{
  const file=new File(["a"],"参考图 {{number}}.png",{type:"image/png"});
  let current:ReferenceAttachment[]=[{key:"one",file,usage:"发送 {{maximum}} <b>user</b>"}];
  const changed=vi.fn();
  function Harness(){const [items,setItems]=useState(current);current=items;return <ReferenceAttachments
    items={items} onChange={next=>{changed(next);setItems(next);}} maximum={3} fileInputTestId="files"
    options={[{key:"custom",label:"参考图 1"},{key:"generated",label:"参考图 2",referenceNumber:2}]}/>;}
  render(<Harness/>);
  fireEvent.change(screen.getByTestId("files"),{target:{files:[new File(["x"],"{{maximum}}.txt",{type:"text/plain"})]}});
  expect(screen.getAllByRole("alert").at(-1)!).toHaveTextContent("{{maximum}}.txt");
  await act(async()=>{await i18n.changeLanguage("en");});
  expect(screen.getByRole("textbox",{name:"Purpose of reference 1"})).toHaveValue("发送 {{maximum}} <b>user</b>");
  expect(screen.getByText("参考图 {{number}}.png")).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"参考图 1",hidden:true})).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"Reference 2",hidden:true})).toBeInTheDocument();
  fireEvent.change(screen.getByTestId("files"),{target:{files:[file,file,file]}});
  expect(screen.getAllByRole("alert").map(item=>item.textContent).join("|")).toContain("Choose up to 3 references");
  await act(async()=>{await i18n.changeLanguage("ja");});
  expect(changed).not.toHaveBeenCalled();
  expect(current).toEqual([{key:"one",file,usage:"发送 {{maximum}} <b>user</b>"}]);
});
it("keeps a failed upload and its retry identity across a language change",async()=>{
  const ref=createRef<ReferenceAttachmentsHandle>(),file=new File(["a"],"one.png",{type:"image/png"});
  let current:ReferenceAttachment[]=[{key:"one",file,usage:"色"}];
  const upload=vi.fn().mockResolvedValue({id:"asset-one"});
  function Harness(){const [items,setItems]=useState(current);current=items;return <ReferenceAttachments
    ref={ref} items={items} onChange={setItems} maximum={2} upload={upload}/>;}
  render(<Harness/>);
  act(()=>ref.current!.markUploadFailure("one",file));
  await act(async()=>{await i18n.changeLanguage("en");});
  expect(screen.getAllByRole("alert").at(-1)!).toHaveTextContent("Upload incomplete");
  await act(async()=>{await i18n.changeLanguage("ja");});
  expect(upload).not.toHaveBeenCalled();
  await act(async()=>{fireEvent.click(screen.getByRole("button",{name:"参考画像 1 を再アップロード"}));});
  expect(upload).toHaveBeenCalledExactlyOnceWith(file);
  expect(current).toEqual([{key:"one",file,usage:"色",assetId:"asset-one"}]);
});
