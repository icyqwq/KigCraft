import {useMessageCopy} from "./useMessageCopy";
import {useLayoutEffect} from "react";
import {skipToken,useQuery} from "@tanstack/react-query";
import {MessageContent} from "./MessageContent";
import type {Snapshot} from "./api";

export type MessageStream = {sequence:number;branch_id:string;operation_id:string;text:string};
export const messageStreamKey=(owner:string,design:string,message:string)=>["v2",owner,"message-stream",design,message] as const;

export function StreamingMessage({ownerId,designId,message,snapshotSequence,onProgress}:{
  ownerId:string;designId:string;message:Snapshot["messages"][number];snapshotSequence:number;onProgress:()=>void;
}){
  const {c}=useMessageCopy();
  const {data}=useQuery<MessageStream>({queryKey:messageStreamKey(ownerId,designId,message.id),queryFn:skipToken});
  const text=message.status==="streaming"&&data&&data.sequence>snapshotSequence&&data.branch_id===message.branch_id&&data.operation_id===message.operation_id
    ?data.text:undefined;
  useLayoutEffect(()=>{if(text!==undefined)onProgress();},[text,onProgress]);
  return <><MessageContent parts={text===undefined?message.content_parts:[{type:"text",text}]}/>
    {message.status==="interrupted"?<p className="v2-status">{c("interrupted")}</p>:null}</>;
}
