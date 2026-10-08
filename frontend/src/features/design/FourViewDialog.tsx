import {useRef,useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {ErrorToast} from "../../ui/ErrorToast";
import {V2Dialog} from "../../ui/V2Dialog";
import {ReferenceAttachmentsCompact} from "../references/ReferenceAttachmentsCompact";
import type {ReferenceAttachment} from "../references/attachments";
import {designApi,type Revision} from "./api";
import {useViewsetCopy} from "./useViewsetCopy";

/**
 * Asks only for what the sheet needs: extra pictures of a side or the back, and a note. Sending it is an ordinary turn in
 * the conversation (the page hands it to the composer), so the result is an ordinary image on the assistant's side.
 */
export default function FourViewDialog({owner,design,source,canGenerate,busy,closing=false,onClose,onSubmit}:{
 owner:string;design:string;source:string;canGenerate:boolean;busy:boolean;closing?:boolean;onClose:()=>void;
 onSubmit:(request:{revision:Revision;text:string;attachments:ReferenceAttachment[]})=>void;
}){
 const {c,message}=useViewsetCopy();
 const valid=/^[a-zA-Z0-9_-]{1,100}$/.test(source);
 const revision=useQuery({queryKey:["v2",owner,"revision",design,source],queryFn:({signal})=>designApi.revision(design,source,signal),enabled:valid,retry:false,staleTime:Infinity});
 const [attachments,setAttachments]=useState<ReferenceAttachment[]>([]);
 const [notes,setNotes]=useState("");
 const sent=useRef(false);
 const unavailable=!valid||revision.isError||!revision.data||revision.data.view!=="front";
 const generate=<button type="button" className="v2-primary" disabled={unavailable||!canGenerate||busy}
  onClick={()=>{if(sent.current||!revision.data)return;sent.current=true;
   onSubmit({revision:revision.data,text:notes.trim(),attachments:attachments.map(item=>({...item,usage:item.usage.trim()}))});}}>{c("viewQuote")}</button>;
 return <V2Dialog open={!closing} size="md" title={c("create")} onClose={onClose} actions={generate}>
  <div className="v2-viewset-create">
   <ErrorToast message={!valid?c("invalidCreate"):revision.isError?message(revision.error):revision.data&&revision.data.view!=="front"?c("frontRequired"):null}
    action={valid&&revision.isError?{label:c("retryRead"),onClick:()=>void revision.refetch()}:undefined}/>
   {revision.data?<figure className="v2-viewset-source"><img src={revision.data.image_url} alt={c("fixedFront",{number:revision.data.number??c("unknown")})}/></figure>:null}
   <section className="v2-vc-group" aria-label={c("extraRefs")}>
    <h3>{c("extraRefs")}</h3>
    <ReferenceAttachmentsCompact items={attachments} onChange={setAttachments} maximum={5} label={c("extraRefs")} uploadOnly/>
   </section>
   <label className="v2-field"><span>{c("notes")}</span>
    <textarea aria-label={c("notes")} placeholder={c("notesPlaceholder")} rows={4} maxLength={4000} value={notes} onChange={event=>setNotes(event.target.value)}/></label>
  </div>
 </V2Dialog>;
}
