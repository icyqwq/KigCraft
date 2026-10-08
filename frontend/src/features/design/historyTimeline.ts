import type {HistoryPage,Snapshot} from "./api";

export function historyTimeline(pages:HistoryPage[]|undefined,snapshot:Snapshot|undefined){
  // A page refresh may lag or fail while the current snapshot has already advanced.
  const items=[
    ...(snapshot?.messages??[]).map(message=>({type:"message" as const,message})),
    ...(snapshot?.revisions??[]).map(revision=>({type:"revision" as const,revision})),
    ...(pages?.flatMap(page=>page.items)??[]),
  ];
  const unique=new Map<string,HistoryPage["items"][number]>();
  const statusRank=(status:string)=>status==="completed"?2:status==="interrupted"?1:0;
  for(const item of items){
    const key=item.type+":"+(item.type==="message"?item.message.id:item.revision.id);
    const previous=unique.get(key);
    // Final persisted content is immutable and must never revert to a streaming prefix,
    // regardless of which request completed first.
    if(!previous||(item.type==="message"&&previous.type==="message"&&
      statusRank(item.message.status)>statusRank(previous.message.status)))unique.set(key,item);
  }
  return [...unique.values()].map(item=>item.type==="message"
    ? {type:"message" as const,id:item.message.id,date:item.message.created_at,sequence:item.message.history_sequence,message:item.message}
    : {type:"image" as const,id:item.revision.id,date:item.revision.created_at,sequence:item.revision.history_sequence,revision:item.revision})
    .sort((a,b)=>a.sequence!==undefined&&b.sequence!==undefined?a.sequence-b.sequence:a.date.localeCompare(b.date)||a.id.localeCompare(b.id));
}
