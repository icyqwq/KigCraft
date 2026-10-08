import {useState} from "react";
import {V2Dialog} from "../../ui/V2Dialog";
import {useMessageCopy} from "./useMessageCopy";
import {useReferenceCopy} from "../references/useReferenceCopy";
import type {Snapshot} from "./api";
type Parts=Snapshot["messages"][number]["content_parts"];
export function MessageContent({parts}:{parts:Parts}){
 const {c}=useMessageCopy(),r=useReferenceCopy();
 const [zoom,setZoom]=useState<{src:string;label:string}|null>(null);
 return <>{parts.map((part,index)=>part.type==="text"?<p key={index}>{part.text}</p>:
   part.type==="references"?<div className="v2-message-references" key={index}>
    {part.items?.map((reference,position)=><figure key={reference.asset_id} data-reference-number={reference.number??position+1}>
      {(()=>{
       const src="/api/v2/assets/"+encodeURIComponent(reference.asset_id),label=r("number",{number:reference.number??position+1});
       return <button type="button" className="v2-reference-zoom" aria-label={label} aria-haspopup="dialog" onClick={()=>setZoom({src,label})}>
        <img src={src} alt={label} loading="lazy"/></button>;
      })()}
      <figcaption>{r("number",{number:reference.number??position+1})}{reference.usage?" · "+reference.usage:""}</figcaption>
    </figure>)}
   </div>:part.type==="annotations"?<ol className="v2-message-annotations" aria-label={c("annotations")} key={index}>
    {part.annotations?.map(mark=><li key={mark.annotation_id} value={mark.number} data-annotation-id={mark.annotation_id}>
      <span className="v2-annotation-number">{c("annotation",{number:mark.number})}</span>
      {mark.text?<span>{c("imageText",{text:mark.text})}</span>:null}
      {mark.note?<p>{mark.note}</p>:null}
    </li>)}
   </ol>:null)}
  <V2Dialog open={zoom!==null} size="lg" title={zoom?.label??""} onClose={()=>setZoom(null)} className="v2-image-zoom">
   {zoom?<img className="v2-image-zoom-img" src={zoom.src} alt={zoom.label}/>:null}
  </V2Dialog></>;
}
