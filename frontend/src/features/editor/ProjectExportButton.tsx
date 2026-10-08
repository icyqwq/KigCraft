import {useEditorCopy,type EditorCopyKey} from "./useEditorCopy";
import {useEffect,useRef,useState} from "react";
import Dialog from "@mui/material/Dialog";
import {exportProject,projectFileName,type ProjectCapture} from "./io/project";
type Props={capture:()=>ProjectCapture;name:string;disabled?:boolean;resolveAsset?:(id:string,signal:AbortSignal)=>Promise<Blob>};
export function ProjectExportButton({capture,name,disabled,resolveAsset}:Props){
  const {c}=useEditorCopy();
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState<EditorCopyKey|"">("");
  const request=useRef<AbortController|undefined>(undefined),live=useRef(true),flight=useRef(false);
  useEffect(()=>{live.current=true;return()=>{live.current=false;request.current?.abort();};},[]);
  function close(){request.current?.abort();setOpen(false);setError("");}
  async function download(){
    if(flight.current)return;flight.current=true;setBusy(true);setError("");
    const abort=new AbortController();request.current=abort;
    try{
      const snapshot=capture(),blob=await exportProject(snapshot,name,abort.signal,resolveAsset);
      if(!live.current||abort.signal.aborted)return;
      const url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=projectFileName(name);link.click();
      window.setTimeout(()=>URL.revokeObjectURL(url),0);setOpen(false);
    }catch{if(live.current&&!abort.signal.aborted)setError("exportError");}
    finally{flight.current=false;if(live.current)setBusy(false);}
  }
  return <>
    <button type="button" disabled={disabled||busy} onClick={()=>{setError("");setOpen(true);}}>{c("exportProject")}</button>
    <Dialog open={open} onClose={close} aria-labelledby="project-export-title">
      <div className="v2-dialog-body"><h2 id="project-export-title">{c("exportProject")}</h2>
        <p>{c("exportNote")}</p>
        {error?<p role="alert">{error?c(error): ""}</p>:null}
        <div className="v2-actions"><button onClick={close}>{c("cancel")}</button>
          <button disabled={busy} onClick={()=>void download()}>{busy?c("packing"):c("downloadProject")}</button></div>
      </div>
    </Dialog>
  </>;
}
