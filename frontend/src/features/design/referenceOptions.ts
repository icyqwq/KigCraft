import type {Snapshot} from "./api";
import type {ReferenceOption} from "../references/attachments";
export function referenceOptions(snapshot?:Snapshot):ReferenceOption[] {
  return Array.from(new Map((snapshot?.messages??[]).flatMap(message=>message.content_parts.flatMap(part=>part.items??[]))
    .map(item=>[item.asset_id,{key:item.asset_id,label:"参考图",description:item.usage,imageUrl:"/api/v2/assets/"+item.asset_id}])).values())
    .map((item,index)=>({...item,label:"参考图 "+(index+1),referenceNumber:index+1}));
}

/** Picker tiles for "choose from this design": earlier reference uploads first, then generated versions (newest first). */
export function designPickerOptions(snapshot?:Snapshot):ReferenceOption[] {
  const references:ReferenceOption[]=referenceOptions(snapshot).map(option=>({...option,kind:"reference"}));
  const seen=new Set(references.map(option=>option.key));
  const versions:ReferenceOption[]=[];
  for(const revision of [...(snapshot?.revisions??[])].sort((a,b)=>(b.number??0)-(a.number??0))){
    if(seen.has(revision.asset_id))continue;
    seen.add(revision.asset_id);
    versions.push({key:revision.asset_id,label:revision.number===undefined?"":"v"+revision.number,imageUrl:revision.image_url,kind:"version",versionNumber:revision.number});
  }
  return [...references,...versions];
}
