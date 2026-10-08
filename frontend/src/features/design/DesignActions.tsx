import {useDesignCopy} from "./useDesignCopy";
import {ErrorToast} from "../../ui/ErrorToast";
import {useEffect,useRef,useState} from "react";
import {IconDots} from "@tabler/icons-react";
import {V2Dialog} from "../../ui/V2Dialog";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import {designApi,DesignApiError,errorText,type Design,type Operation} from "./api";
export function DesignActions({design,operation,onUpdated,onRename,onRemoved,onHistory,compact=false,deleteBlocked=false}:{
 design:Design;operation?:Operation;onUpdated:()=>Promise<void>;onRename:()=>void;onRemoved:()=>Promise<void>;onHistory?:()=>void;
 /** Hover key in the design list: smaller, and it opens only the menu. */
 compact?:boolean;
 /** The design is generating but its task is unknown here, so it cannot be deleted yet. */
 deleteBlocked?:boolean;
}){
 const {c,language}=useDesignCopy();
 const [anchor,setAnchor]=useState<HTMLElement|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>("");
 const [deleting,setDeleting]=useState(false),inFlight=useRef(false),live=useRef(true);
 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 async function run(action:()=>Promise<unknown>,removed=false){
  if(inFlight.current)return;
  inFlight.current=true;setBusy(true);setError("");setAnchor(null);
  try{await action();if(live.current)await(removed?onRemoved():onUpdated());}
  catch(failure){
   if(removed&&live.current){
    try{await designApi.lifecycle(design.id);}
    catch(check){
     // The design is gone: the delete went through even though its reply was lost.
     if(check instanceof DesignApiError&&check.status===404){if(live.current)await onRemoved();return;}
     /* Any other answer keeps the uncertainty visible; a delete is never resent automatically. */
    }
   }
   if(live.current){setError(failure||"request_failed");await onUpdated();}
  }
  finally{inFlight.current=false;if(live.current)setBusy(false);}
 }
 return <><button className={"v2-quiet v2-icon"+(compact?" v2-list-more":"")} disabled={busy} aria-haspopup="menu" aria-expanded={Boolean(anchor)} aria-label={compact?c("moreActions")+" · "+design.title:c("manage")} title={compact?c("moreActions"):c("manage")} onClick={event=>setAnchor(event.currentTarget)}><IconDots size={18} stroke={1.75}/></button>
  <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={()=>setAnchor(null)}>
   <MenuItem onClick={()=>{setAnchor(null);onRename();}}>{c("rename")}</MenuItem>
   <MenuItem onClick={()=>void run(()=>designApi.update(design,{pinned:!design.pinned}))}>{design.pinned?c("unpin"):c("pin")}</MenuItem>
   {/* Archiving is hidden for now; a design archived earlier can still be restored. */}
   {design.archived_at?<MenuItem onClick={()=>void run(()=>designApi.update(design,{archived:false}))}>{c("restore")}</MenuItem>:null}
   {onHistory?<MenuItem onClick={()=>{setAnchor(null);onHistory();}}>{c("allVersions")}</MenuItem>:null}
   <MenuItem className="v2-menu-danger" disabled={deleteBlocked} title={deleteBlocked?c("deleteBlocked"):undefined} onClick={()=>{setAnchor(null);setError("");setDeleting(true);}}>{c("delete")}</MenuItem>
  </Menu>
  <ErrorToast message={error?errorText(error,language):null}/>
  <V2Dialog open={deleting} danger dismissible={!busy} title={c("deleteTitle")} onClose={()=>setDeleting(false)}
   actions={<>
    <button disabled={busy} onClick={()=>setDeleting(false)}>{c("cancel")}</button>
    {operation?<button disabled={busy||operation.state==="cancelling"} onClick={()=>void run(()=>designApi.cancel(operation.id))}>{c("stopTask")}</button>:
     <button className="v2-primary" disabled={busy} onClick={()=>void run(()=>designApi.remove(design),true)}>{c("delete")}</button>}
   </>}>
   <p>{design.title}</p>
   <p>{c("deleteNote")}</p>
   {operation?<p role="status">{c(operation.state==="cancelling"?"stoppingBeforeDelete":"stopBeforeDelete")}</p>:null}
  </V2Dialog>
 </>;
}
