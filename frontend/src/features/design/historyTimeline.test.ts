import {expect,it} from "vitest";
import {historyTimeline} from "./historyTimeline";
import type {HistoryPage,Snapshot} from "./api";

function message(id:string,sequence:number,status:string,text:string):Snapshot["messages"][number]{
 return {id,history_sequence:sequence,role:"assistant",status,created_at:"2026-01-01",operation_id:"operation",content_parts:[{type:"text",text}]};
}
function page(messages:Snapshot["messages"]):HistoryPage{
 return {items:messages.map(message=>({type:"message",message})),watermark:10,next_cursor:null,ancestry_incomplete:false};
}
function snapshot(messages:Snapshot["messages"]):Snapshot{
 return {id:"design",title:"Design",last_branch_id:"branch",pinned:false,metadata_version:0,updated_at:"",snapshot_sequence:20,
  branch:{id:"branch",state_version:1,current_revision_id:null},messages,revisions:[],operations:[]};
}
it("keeps loaded older records while adding the completed snapshot tail exactly once",()=>{
 const old=message("old",1,"completed","Old"),prefix=message("reply",2,"streaming","Partial");
 const final=message("reply",2,"completed","Complete"),next=message("next",3,"completed","Next");
 const result=historyTimeline([page([old,prefix])],snapshot([final,next]));
 expect(result.map(r=>r.id)).toEqual(["old","reply","next"]);
 expect(result[1].type==="message"&&result[1].message.content_parts).toEqual(final.content_parts);
});
it("a late older snapshot cannot turn completed history back into a streaming prefix",()=>{
 const final=message("reply",2,"completed","Complete"),prefix=message("reply",2,"streaming","Partial");
 const result=historyTimeline([page([final])],snapshot([prefix]));
 expect(result).toHaveLength(1);
 expect(result[0].type==="message"&&result[0].message).toEqual(final);
});
it("focused history without a snapshot remains confined to the requested records",()=>{
 const old=message("old",1,"completed","Old");
 expect(historyTimeline([page([old])],undefined).map(r=>r.id)).toEqual(["old"]);
});
