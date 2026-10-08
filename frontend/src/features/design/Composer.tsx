import { Spinner } from "../../ui/Spinner";
import {ErrorToast} from "../../ui/ErrorToast";
import {useComposerCopy} from "./useComposerCopy";
import { lazy, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type FormEvent, type Ref } from "react";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import { IconArrowUp, IconCheck, IconChevronDown, IconPlayerStopFilled, IconX } from "@tabler/icons-react";
import type { SubmitTurn } from "../../contracts/v2";
import { designApi, DesignApiError, errorText, type Revision, type Snapshot, type HistoryTarget, type MessageEditPreparation } from "./api";
import { designPickerOptions } from "./referenceOptions";
import type { ReferenceAttachmentsHandle } from "../references/ReferenceAttachments";
import { ReferenceAttachmentsCompact } from "../references/ReferenceAttachmentsCompact";
import { ReferenceUploader, ReferenceUploadFailure, type ReferenceAttachment as Attachment } from "../references/attachments";
import {composerScopeKey,emptyComposerDraft} from "./composerDraft";
import {useComposerDraft} from "./useComposerDraft";

const LegacyMaterials=lazy(()=>import("../references/LegacyMaterialsPanel"));

export type FirstStep = "reference" | "head_shell";
export type ComposerHandle = { addFiles: (files: Iterable<File>) => void };
type Props = {
  snapshot?: Snapshot; userId?: string; canGenerate: boolean; busy: boolean; maxInputImages?: number;
  requireAuth: (action: () => void | Promise<void>) => void;
  onSubmitted: (designId: string, branchId?: string) => Promise<void>;
  historyAction?: HistoryTarget | null;
  editMessage?:MessageEditPreparation;
  answerQuestion?:NonNullable<SubmitTurn["answer_to"]>;
  onSignIn?: (action: () => void | Promise<void>) => void;
  /** While a task runs the send key turns into a stop key; the input stays editable for the next turn. */
  onStop?: () => void;
  stopping?: boolean;
  handle?: Ref<ComposerHandle>;
  /** Inline editors show a Cancel key beside Send. */
  onCancel?: () => void;
  /** Fill the draft and send it once: "make a head shell from this reference". Goes through the normal quote and submit path. */
  autoSend?: { id: string; text: string; target: HistoryTarget; kind?: "head_shell" | "four_view"; attachments?: Attachment[] };
  /** New design only: the first-step choice (flat reference first, or straight to the head shell). */
  targetKind?: FirstStep;
};
class SubmitFailure{constructor(readonly original:unknown){}}
export function Composer(props:Props){
  const scope={ownerId:props.userId??null,designId:props.snapshot?.id??null,branchId:props.snapshot?.branch.id??null,...(props.editMessage?{purpose:(props.answerQuestion?"question-answer:":"message-edit:")+props.editMessage.message_id}:{})};
  return <ScopedComposer key={composerScopeKey(scope)} {...props}/>;
}
function ScopedComposer({snapshot,userId,canGenerate,busy,requireAuth,onSignIn,onSubmitted,historyAction,editMessage,answerQuestion,maxInputImages=8,onStop,stopping=false,handle,onCancel,autoSend,targetKind}:Props) {
  const scope={ownerId:userId??null,designId:snapshot?.id??null,branchId:snapshot?.branch.id??null,...(editMessage?{purpose:(answerQuestion?"question-answer:":"message-edit:")+editMessage.message_id}:{})};
  const {c,language}=useComposerCopy();
  const storage=useComposerDraft();
  const {text,attachments,target,pending}=storage.draft,noBase=storage.draft.noBase===true;
  const [baseMenu,setBaseMenu]=useState<HTMLElement|null>(null);
  const [preparing,setPreparing]=useState(false),[error,setError]=useState<unknown>(""),[needsLogin,setNeedsLogin]=useState(false);
  const created=useRef<{id:string;branch:string}|null>(null);
  const inFlight=useRef(false),liveBusy=useRef(busy);liveBusy.current=busy;
  const references=useRef<ReferenceAttachmentsHandle>(null),uploader=useRef(new ReferenceUploader());
  const uploadLifetime=useRef<AbortController|null>(null),field=useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(handle,()=>({
    addFiles:files=>{if(!lockedRef.current)references.current?.addFiles(files);},
  }),[]);
  useEffect(()=>{
    const controller=new AbortController();uploadLifetime.current=controller;
    return()=>controller.abort();
  },[]);
  async function uploadReference(file:Blob){
    const signal=uploadLifetime.current!.signal;
    signal.throwIfAborted();
    const asset=await designApi.upload(file,userId!,signal);
    signal.throwIfAborted();
    return asset;
  }
  const locked=preparing||Boolean(pending)||Boolean(editMessage&&target?.kind!=="message");
  const lockedRef=useRef(locked);lockedRef.current=locked;
  const regenerating=target?.kind==="regenerate";
  useLayoutEffect(()=>{
    const el=field.current;if(!el)return;
    const fit=()=>{el.style.height="auto";el.style.height=Math.min(el.scrollHeight,200)+"px";};
    fit();
    // A first measurement can happen before the box has its final width (fonts, drawer, layout); refit when it changes.
    if(typeof ResizeObserver==="undefined")return;
    let width=el.clientWidth;
    const observer=new ResizeObserver(()=>{if(el.clientWidth!==width){width=el.clientWidth;fit();}});
    observer.observe(el);
    return()=>observer.disconnect();
  },[text,regenerating]);
  const maximum=Math.max(0,maxInputImages-(editMessage?.reserved_images??(!noBase&&(target?.revisionId||snapshot?.branch.current_revision_id)?1:0)));
  const currentBase=snapshot?.current_revision??snapshot?.revisions.find(revision=>revision.id===snapshot.branch.current_revision_id);
  const handledHistory=useRef<string | undefined>(undefined);
  const initializedMessage=useRef<string|undefined>(undefined);
  useEffect(()=>{
    if(!editMessage||initializedMessage.current===editMessage.message_id)return;
    initializedMessage.current=editMessage.message_id;
    if(storage.current.current.pending)return;
    storage.update(draft=>{
      if(draft.target?.kind==="message"&&draft.target.messageId===editMessage.message_id)
        return {...draft,target:{...draft.target,branchVersion:editMessage.branch_version}};
      return {...draft,text:editMessage.text,attachments:editMessage.references.map(item=>({key:item.asset_id,assetId:item.asset_id,usage:item.usage})),
        target:{id:crypto.randomUUID(),kind:"message",messageId:editMessage.message_id,revisionId:editMessage.source_revision_id,
          imageUrl:editMessage.base_image_url,branchVersion:editMessage.branch_version}};
    });
  },[editMessage]);
  useEffect(()=>{
    if(historyAction&&!locked&&handledHistory.current!==historyAction.id){
      handledHistory.current=historyAction.id;storage.update(draft=>({...draft,target:historyAction,noBase:false}));setError("");
    }
  },[historyAction?.id,locked]);
  const autoStarted=useRef<string|undefined>(undefined),autoPending=useRef<string|undefined>(undefined);
  useEffect(()=>{
    if(!autoSend||locked||autoStarted.current===autoSend.id)return;
    autoStarted.current=autoSend.id;autoPending.current=autoSend.id;setError("");
    storage.update(draft=>({...draft,text:autoSend.text,target:autoSend.target,noBase:false,...(autoSend.attachments?{attachments:autoSend.attachments}:{})}));
  },[autoSend?.id,locked]);
  useEffect(()=>{
    if(!autoSend||autoPending.current!==autoSend.id||locked||busy||!canGenerate)return;
    if(text!==autoSend.text||target?.id!==autoSend.target.id)return;
    autoPending.current=undefined;send();
  });
  function setAttachments(next:Attachment[]|((items:Attachment[])=>Attachment[])){
    storage.update(draft=>({...draft,attachments:typeof next==="function"?next(draft.attachments):next}));
  }
  function addFiles(files:FileList|File[]|null){if(files&&!locked)references.current?.addFiles(files);}
  async function prepare(){
    if(!storage.live.current||inFlight.current||liveBusy.current||
      (!regenerating&&!text.trim()&&!attachments.length))return;
    if(!userId)return;
    if(pending){setError("");return;}
    inFlight.current=true;setPreparing(true);setError("");
    try{
      if(editMessage&&target?.kind!=="message")return;
      if(!regenerating&&attachments.length>maximum)throw new DesignApiError("reference_limit_exceeded",422);
      const ready=regenerating?[]:await uploader.current.prepare(attachments,uploadReference,item=>{
        if(storage.live.current)setAttachments(current=>current.map(old=>old.key===item.key&&old.file===item.file?{...old,assetId:item.assetId}:old));
      });
      if(!storage.live.current)return;
      if(!regenerating)await storage.change(draft=>({...draft,attachments:ready}));
      if(!storage.live.current)return;
      if(!snapshot&&!created.current){
        const design=await designApi.create(storage.current.current.createKey,text.trim().slice(0,40)||"新设计");
        if(!storage.live.current)return;
        created.current={id:design.id,branch:design.last_branch_id};
      }
      const designId=snapshot?.id??created.current!.id,branchId=snapshot?.branch.id??created.current!.branch;
      // Edits keep the first-step choice they were sent with; "make the head shell" is explicit; only a new design uses the entry choice.
      const turnKind=answerQuestion||regenerating?undefined:editMessage?editMessage.target_kind??undefined:
        autoSend&&target?.id===autoSend.target.id?(autoSend.kind??"head_shell") as "head_shell"|"four_view":!snapshot?targetKind:undefined;
      const payload:Omit<SubmitTurn,"quote_id">={
        ...(answerQuestion?{answer_to:answerQuestion}:{}),
        schema_version:1,client_request_id:crypto.randomUUID(),intent:"generate_or_clarify",text:regenerating?"":text,
        base_revision_id:regenerating||noBase?null:target?.kind==="message"?target.revisionId:target?.revisionId??snapshot?.branch.current_revision_id??null,
        expected_branch_version:target?.branchVersion??snapshot?.branch.state_version??0,
        continuation:answerQuestion?null:target?.kind==="message"?{source_revision_id:target.revisionId,boundary_message_id:target.messageId}:target?.kind==="continue"?{source_revision_id:target.revisionId,boundary_message_id:null}:null,
        regenerate_revision_id:regenerating?target.revisionId:null,annotation_asset_id:null,mask_asset_id:null,
        ...(turnKind?{target_kind:turnKind}:{}),
        attachments:ready.map(item=>({asset_id:item.assetId!,usage:item.usage})),
      };
      const quote=await designApi.quote(branchId,payload);
      if(!storage.live.current)return;
      // The quote's max_units is the price cap the server enforces; pressing Send is the user's consent.
      await submitPending({designId,branchId,payload:{...payload,quote_id:quote.id},
        units:quote.max_units,attempted:true,preview:quote.request_preview});
    }catch(failure){
      if(!storage.live.current)return;
      if(failure instanceof ReferenceUploadFailure)references.current?.markUploadFailure(failure.key,failure.file);
      else if(failure instanceof SubmitFailure){handleSubmitFailure(failure.original);return;}
      setError((failure instanceof ReferenceUploadFailure?failure.original:failure)||"request_failed");
    }finally{inFlight.current=false;if(storage.live.current)setPreparing(false);}
  }
  async function submitPending(attempt:NonNullable<typeof pending>){
    try{
      // A durable receipt must exist before a potentially billable HTTP request.
      await storage.change(draft=>({...draft,pending:attempt}));
      if(!storage.live.current)return;
      const operation=await designApi.submit(attempt.branchId,attempt.payload);
      if(!storage.live.current)return;
      await storage.commit(draft=>attempt.payload.regenerate_revision_id
        ?{...draft,pending:null,target:null,createKey:crypto.randomUUID()}:emptyComposerDraft());
      if(!storage.live.current)return;
      created.current=null;
      await onSubmitted(attempt.designId,operation.branch_id);
    }catch(failure){throw new SubmitFailure(failure);}
  }
  function handleSubmitFailure(failure:unknown){
    if(!storage.live.current)return;
    setError(failure||"request_failed");
    if(failure instanceof DesignApiError&&failure.status===401)setNeedsLogin(true);
    else if(failure instanceof DesignApiError&&failure.status>=400&&failure.status<500&&failure.code!=="idempotency_conflict")
      storage.update(draft=>({...draft,pending:null}));
  }
  async function confirm(){
    if(!pending||inFlight.current||!storage.live.current||!userId)return;
    inFlight.current=true;setPreparing(true);setError("");setNeedsLogin(false);
    try{await submitPending({...pending,attempted:true});}
    catch(failure){handleSubmitFailure(failure instanceof SubmitFailure?failure.original:failure);}
    finally{inFlight.current=false;if(storage.live.current)setPreparing(false);}
  }
  const baseChoices=[...(snapshot?.revisions??[])].filter(revision=>revision.can_continue!==false).sort((a,b)=>(b.number??0)-(a.number??0)).slice(0,12);
  function chooseBase(revision:Revision|null){
    setBaseMenu(null);
    if(!revision){storage.update(draft=>({...draft,target:null,noBase:true}));return;}
    if(revision.id===snapshot?.branch.current_revision_id){storage.update(draft=>({...draft,target:null,noBase:false}));return;}
    storage.update(draft=>({...draft,noBase:false,target:{id:crypto.randomUUID(),kind:"continue",revisionId:revision.id,imageUrl:revision.image_url,branchVersion:snapshot!.branch.state_version}}));
  }
  function send(event?:FormEvent){
    event?.preventDefault();
    if(locked||busy||!canGenerate)return;
    // Login restores a separate owner scope; adopting guest input is an explicit action there.
    requireAuth(userId?prepare:()=>{});
  }
  return <form className="v2-composer" onSubmit={send} onDragOver={event=>event.preventDefault()}
    onDrop={event=>{event.preventDefault();addFiles(event.dataTransfer.files);}}>
    {!editMessage&&!regenerating&&snapshot?.branch.current_revision_id?(()=>{
      const picked=target?.kind==="continue"?target:null;
      const baseImage=picked?picked.imageUrl:currentBase?.image_url;
      const baseNumber=picked?snapshot.revisions.find(revision=>revision.id===picked.revisionId)?.number:currentBase?.number;
      return <div className="v2-continuation v2-base-chip" title={picked?c("continueFrom"):undefined}>
        <button type="button" className="v2-quiet v2-base-button" aria-label={c("chooseBase")} aria-haspopup="menu" aria-expanded={Boolean(baseMenu)}
          disabled={locked} onClick={event=>setBaseMenu(event.currentTarget)}>
          {noBase?<span>{c("noBase")}</span>:<>{baseImage?<img src={baseImage} alt={c(picked?"selectedVersion":"baseImage")}/>:null}
            {baseNumber!==undefined?<span className="v2-version-stamp">v{baseNumber}</span>:null}</>}
          <IconChevronDown size={16} stroke={1.75}/>
        </button>
        {picked?<button type="button" className="v2-quiet v2-icon" aria-label={c("cancelSelection")} title={c("cancelSelection")} disabled={locked}
          onClick={()=>storage.update(draft=>({...draft,target:null}))}><IconX size={16} stroke={1.75}/></button>:null}
        <Menu anchorEl={baseMenu} open={Boolean(baseMenu)} onClose={()=>setBaseMenu(null)}>
          {baseChoices.map(revision=>{
            const selected=!noBase&&(picked?picked.revisionId===revision.id:revision.id===snapshot.branch.current_revision_id);
            return <MenuItem key={revision.id} selected={selected} className="v2-base-option" onClick={()=>chooseBase(revision)}>
              <img src={revision.image_url} alt=""/><span className="v2-version-stamp">{revision.number!==undefined?c("baseOption",{number:revision.number}):""}</span>
              {selected?<IconCheck size={16} stroke={1.75}/>:null}</MenuItem>;
          })}
          <MenuItem selected={noBase} onClick={()=>chooseBase(null)}>{c("noBase")}{noBase?<IconCheck size={16} stroke={1.75}/>:null}</MenuItem>
        </Menu>
      </div>;
    })():null}
    {target&&(target.kind==="message"||regenerating)?<div className="v2-continuation v2-base-chip" role="status">{target.imageUrl?<img src={target.imageUrl} alt={target.kind==="message"?c("originalBase"):c("selectedVersion")}/>:null}
      {regenerating?<span>{c("regenerate")}</span>:<span>{answerQuestion?c("answerQuestion"):c("editMessage")}</span>}
      {target.kind!=="message"?<button type="button" className="v2-quiet v2-icon" aria-label={c("cancelSelection")} title={c("cancelSelection")} disabled={locked}
        onClick={()=>storage.update(draft=>({...draft,target:null}))}><IconX size={16} stroke={1.75}/></button>:null}</div>:null}
    {editMessage?.local_edit_mode?<p className="v2-status v2-composer-note">{c(editMessage.local_edit_mode==="lock_outside"?"lockMode":"preserveMode")}</p>:null}
    {!regenerating?<ReferenceAttachmentsCompact ref={references} items={attachments} onChange={setAttachments}
      options={designPickerOptions(snapshot)} maximum={maximum} disabled={locked} legacyOwnerId={userId} library={LegacyMaterials} upload={userId?uploadReference:undefined}/>:null}
    {!regenerating?<textarea ref={field} aria-label={answerQuestion?c("answer"):c("requirements")} placeholder={c("placeholder")} value={text} rows={1}
      disabled={locked} maxLength={16000} onChange={event=>storage.update(draft=>({...draft,text:event.target.value}))}
      onPaste={event=>{if(event.clipboardData.files.length){event.preventDefault();addFiles(event.clipboardData.files);}}}
      onKeyDown={event=>{
        // The final IME Enter can follow compositionend, when isComposing is already false.
        if(event.key==="Enter"&&!event.shiftKey&&!event.nativeEvent.isComposing&&event.nativeEvent.keyCode!==229){
          event.preventDefault();send();
        }
      }}/>:null}
    {pending&&!preparing?<div className="v2-pending" role="group" aria-label={c("confirm")}>
      <span>{c("pendingAttempted")}</span>
      {!pending.attempted?<button type="button" onClick={()=>storage.update(draft=>({...draft,pending:null}))}>{c("backEdit")}</button>:null}
      <button type="button" className="v2-primary" onClick={()=>needsLogin&&onSignIn?onSignIn(confirm):void confirm()}>
        {needsLogin?c("signInContinue"):pending.attempted?c("retrySend"):c("send")}</button>
    </div>:null}
    <ErrorToast message={error?errorText(error,language):null}/>
    <div className="v2-composer-bar">
      <span className="v2-composer-hint"/>
      {onCancel?<button type="button" className="v2-quiet v2-cancel" onClick={onCancel}>{c("cancel")}</button>:null}
      {busy&&onStop&&!editMessage?<button className="v2-primary v2-send v2-stop" type="button" disabled={stopping} aria-label={c(stopping?"stopping":"stop")} title={c(stopping?"stopping":"stop")} onClick={onStop}>
        {stopping?<Spinner size={16}/>:<IconPlayerStopFilled size={16}/>}</button>
      :<button className="v2-primary v2-send" type="submit" disabled={!canGenerate||locked||busy||
        (!regenerating&&(attachments.length>maximum||(!text.trim()&&!attachments.length)))} aria-label={c("send")} title={c("send")}>
        {preparing?<Spinner size={18}/>:<IconArrowUp size={18} stroke={1.75}/>}</button>}
    </div>
  </form>;
}
