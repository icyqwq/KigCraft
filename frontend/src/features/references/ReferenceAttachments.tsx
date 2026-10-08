import { forwardRef, Suspense, useEffect, useImperativeHandle, useRef, useState, type ComponentType } from "react";
import {ErrorToast} from "../../ui/ErrorToast";
import { ReferenceSelectionError, appendReferenceFiles, type ReferenceAttachment, type ReferenceOption } from "./attachments";
import "./references.css";
import {useReferenceCopy} from "./useReferenceCopy";
import {V2Dialog} from "../../ui/V2Dialog";
export type ReferenceLibraryProps = {
  ownerId: string; onChoose: (option: ReferenceOption) => void;
  selected: string[]; maximum: number; disabled: boolean;
};
export type ReferenceAttachmentsHandle = { addFiles: (files: Iterable<File>)=>void; markUploadFailure:(key:string,file:File)=>void };
export function Preview({item,option,index}:{item:ReferenceAttachment;option?:ReferenceOption;index:number}) {
  const c=useReferenceCopy();
  const [url,setUrl]=useState<string>();
  useEffect(()=>{
    if (!item.file) {setUrl(undefined);return;}
    const next=URL.createObjectURL(item.file);setUrl(next);return()=>URL.revokeObjectURL(next);
  },[item.file]);
  const source=url ?? option?.imageUrl ?? (item.assetId ? "/api/v2/assets/"+encodeURIComponent(item.assetId):undefined);
  return source ? <img src={source} alt={c("number",{number:index+1})} /> : <span>{c("number",{number:index+1})}</span>;
}
export const ReferenceAttachments=forwardRef<ReferenceAttachmentsHandle,{
  items:ReferenceAttachment[];onChange:(items:ReferenceAttachment[])=>void;maximum:number;disabled?:boolean;
  library?:ComponentType<ReferenceLibraryProps>;legacyOwnerId?:string;options?:ReferenceOption[];label?:string;fileInputTestId?:string;upload?:(file:File)=>Promise<{id:string}>;
}>(function ReferenceAttachments({items,onChange,maximum,disabled=false,options=[],label,fileInputTestId,upload,legacyOwnerId,library:LegacyMaterials},ref) {
  const c=useReferenceCopy();
  const [legacyOpen,setLegacyOpen]=useState(false);
  useEffect(()=>setLegacyOpen(false),[legacyOwnerId]);
  const input=useRef<HTMLInputElement>(null), replace=useRef<HTMLInputElement>(null);
  const [replaceKey,setReplaceKey]=useState<string>();
  const [error,setError]=useState<ReferenceSelectionError | "unknown" | null>(null);
  const selectionError=(failure:unknown)=>setError(failure instanceof ReferenceSelectionError?failure:"unknown");
  const errorMessage=error instanceof ReferenceSelectionError
    ?c(error.reason,{maximum:error.maximum,files:error.files.join("、")})
    :error?c("selectionFailed"):items.length>maximum?c("overLimit"):"";
  const [failed,setFailed]=useState<Record<string,File>>({});
  const [uploading,setUploading]=useState<string[]>([]);
  const inflight=useRef(new Set<string>()),live=useRef(true),latest=useRef(items); latest.current=items;
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  async function retry(item:ReferenceAttachment) {
    if(!upload||!item.file||disabled||inflight.current.has(item.key))return;
    inflight.current.add(item.key);setUploading([...inflight.current]);
    try {
      const asset=await upload(item.file);
      if(live.current)onChange(latest.current.map(old=>old.key===item.key&&old.file===item.file?{...old,assetId:asset.id}:old));
    }catch {if(live.current)setFailed(current=>({...current,[item.key]:item.file!}));}
    finally{inflight.current.delete(item.key);if(live.current)setUploading([...inflight.current]);}
  }
  function addFiles(files:Iterable<File>) {
    if(disabled)return;
    const selected=Array.from(files);if(!selected.length)return;
    try {onChange(appendReferenceFiles(items,selected,maximum));setError(null);}
    catch(error){selectionError(error);}
  }
  useImperativeHandle(ref,()=>({addFiles,markUploadFailure:(key,file)=>setFailed(current=>({...current,[key]:file}))}),[items,maximum,disabled,onChange]);
  function toggle(option:ReferenceOption) {
    if(disabled)return;
    const exists=items.some(item=>item.assetId===option.key);
    if(exists){onChange(items.filter(item=>item.assetId!==option.key));setError(null);return;}
    if(items.length>=maximum){setError(new ReferenceSelectionError("limit",maximum));return;}
    onChange([...items,{key:crypto.randomUUID(),assetId:option.key,usage:option.description??""}]);setError(null);
  }
  return <section className="reference-attachments" aria-label={label??c("references")}
    onDragOver={event=>{event.preventDefault();event.stopPropagation();}}
    onDrop={event=>{event.preventDefault();event.stopPropagation();addFiles(event.dataTransfer.files);}}
    onPaste={event=>{if(event.clipboardData.files.length){event.preventDefault();event.stopPropagation();addFiles(event.clipboardData.files);}}}>
    <div className="reference-attachment-list">{items.map((item,index)=><div className="reference-attachment" key={item.key}>
      <Preview item={item} option={options.find(option=>option.key===item.assetId)} index={index}/>
      <div className="reference-attachment-content">
        <span className="reference-number">{c("number",{number:index+1})}</span>
        {item.file ? <span className="reference-file-name">{item.file.name}</span>:null}
        <input aria-label={c("usage",{number:index+1})} placeholder={c("usagePlaceholder")} value={item.usage} maxLength={2000}
          disabled={disabled} onChange={event=>onChange(items.map(old=>old.key===item.key?{...old,usage:event.target.value}:old))}/>
        <ErrorToast message={failed[item.key]===item.file && item.file && !item.assetId ? c("uploadFailed") : null}
          action={upload?{label:c("retryNumber",{number:index+1}),onClick:()=>void retry(item)}:undefined}/>
        <div><button type="button" disabled={disabled} aria-label={c("replaceNumber",{number:index+1})}
            onClick={()=>{setReplaceKey(item.key);replace.current?.click();}}>{c("replace")}</button>
          <button type="button" disabled={disabled} aria-label={c("removeNumber",{number:index+1})}
            onClick={()=>onChange(items.filter(old=>old.key!==item.key))}>{c("remove")}</button></div>
      </div>
    </div>)}</div>
    <input ref={input} data-testid={fileInputTestId} type="file" multiple hidden accept="image/png,image/jpeg,image/webp"
      onChange={event=>{addFiles(event.target.files??[]);event.target.value="";}}/>
    <input ref={replace} type="file" hidden aria-label={c("replaceFile")} accept="image/png,image/jpeg,image/webp"
      onChange={event=>{
        const file=event.target.files?.[0];event.target.value="";if(!file||!replaceKey||disabled)return;
        try {
          const valid=appendReferenceFiles([], [file],1)[0];
          onChange(items.map(old=>old.key===replaceKey?{key:old.key,usage:old.usage,file:valid.file}:old));setError(null);
        }catch(error){selectionError(error);}
      }}/>
    <button type="button" disabled={disabled||items.length>=maximum} onClick={()=>input.current?.click()}>{c("upload")}</button>
    {legacyOwnerId && LegacyMaterials?<><button type="button" disabled={disabled} onClick={()=>setLegacyOpen(true)}>{c("legacyChoose")}</button>
      <V2Dialog open={legacyOpen} size="md" className="legacy-materials-dialog" labelId="legacy-reference-title" title={c("legacyChoose")} onClose={()=>setLegacyOpen(false)}
        actions={<button type="button" className="v2-primary" onClick={()=>setLegacyOpen(false)}>{c("legacyDone")}</button>}>
        {legacyOpen?<Suspense fallback={<p role="status">{c("legacyLoading")}</p>}>
          <LegacyMaterials key={legacyOwnerId} ownerId={legacyOwnerId} onChoose={toggle}
            selected={items.flatMap(item=>item.assetId?[item.assetId]:[])}
            maximum={maximum-items.filter(item=>!item.assetId).length} disabled={disabled}/>
        </Suspense>:null}
      </V2Dialog></>:null}
    {options.length ? <details><summary>{c("existing")}</summary><div className="reference-options">
      {options.map(option=><button type="button" key={option.key} disabled={disabled}
        data-testid={"editor-local-reference-"+option.key} aria-pressed={items.some(item=>item.assetId===option.key)}
        onClick={()=>toggle(option)}><img src={option.imageUrl??"/api/v2/assets/"+encodeURIComponent(option.key)} alt="" width={36} height={36}/>{option.referenceNumber===undefined?option.label:c("number",{number:option.referenceNumber})}</button>)}
    </div></details>:null}
    <span className="reference-count">{items.length}/{maximum}</span>
    <ErrorToast message={errorMessage||null}/>
  </section>;
});
