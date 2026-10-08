import {cleanup,fireEvent,render,screen} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {OperationFailureNotice} from "./OperationFailureNotice";
import type {Operation,Snapshot} from "./api";
import zh from "./message-copy-zh-CN";
const operation:Operation={id:"failed",branch_id:"branch",kind:"generate",state:"failed",base_revision_id:null,reserved_units:1,settled_units:0,error_code:"moderation_unavailable"};
const messages:Snapshot["messages"]=[
 {id:"other",role:"user",status:"completed",created_at:"",operation_id:"different",content_parts:[],can_edit:true},
 {id:"source",role:"user",status:"completed",created_at:"",operation_id:"failed",content_parts:[],can_edit:true},
];
afterEach(cleanup);
it("opens only the failed request on explicit recovery",()=>{
 const recover=vi.fn();
 render(<OperationFailureNotice operation={operation} messages={messages} stateText="Failed" canRecover onRecover={recover}/>);
 expect(screen.getByRole("alert")).toHaveTextContent(zh.reviewNoCharge);
 expect(recover).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:zh.reviewRecover}));
 expect(recover).toHaveBeenCalledExactlyOnceWith("source");
});
it.each(["runtime_unavailable","moderation_rejected"])("limits free-review guidance to known review failure: %s",code=>{
 render(<OperationFailureNotice operation={{...operation,error_code:code,settled_units:1}} messages={messages} stateText="Failed" canRecover onRecover={()=>{}}/>);
 expect(screen.getByRole("alert")).not.toHaveTextContent(zh.reviewNoCharge);
 expect(screen.queryByRole("button")!==null).toBe(code==="moderation_rejected");
});
it("does not offer recovery for unknown execution or an unavailable source",()=>{
 const view=render(<OperationFailureNotice operation={{...operation,state:"reconciling"}} messages={messages} stateText="Checking" canRecover onRecover={()=>{}}/>);
 expect(screen.queryByRole("button")).toBeNull();
 view.rerender(<OperationFailureNotice operation={operation} messages={messages.map(m=>({...m,can_edit:false}))} stateText="Failed" canRecover onRecover={()=>{}}/>);
 expect(screen.queryByRole("button")).toBeNull();
});
it("offers no recovery while the design cannot accept new requests",()=>{
 const recover=vi.fn();
 render(<OperationFailureNotice operation={operation} messages={messages} stateText="Failed" canRecover={false} onRecover={recover}/>);
 expect(screen.queryByRole("button")).toBeNull();expect(recover).not.toHaveBeenCalled();
});
it("explains an injection rejection neutrally and offers the same edit-and-retry recovery",()=>{
 render(<OperationFailureNotice operation={{...operation,error_code:"prompt_injection_rejected"}} messages={messages} stateText="Failed" canRecover onRecover={()=>{}}/>);
 expect(screen.getByRole("alert")).toHaveTextContent("请求包含不支持的指令，请只描述设计内容。");
 expect(screen.getByRole("alert")).toHaveTextContent(zh.reviewNoCharge);
 expect(screen.getByRole("button",{name:zh.reviewRecover})).toBeInTheDocument();
});
