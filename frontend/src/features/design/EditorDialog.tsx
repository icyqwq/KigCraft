import {unchangedEditorImage} from "./unchangedEditorImage";
import {ErrorToast} from "../../ui/ErrorToast";
import {useCloudEditorCopy} from "./useCloudEditorCopy";
import {editorViewTools} from "./editorViewTools";
import {MessageContent} from "./MessageContent";
import {numberedAnnotations} from "./messageBindings";
import {RequestPreview} from "./RequestPreview";
import {ProjectExportButton} from "../editor/ProjectExportButton";
import {cloudDraftApi} from "../editor/drafts/cloudApi";
import {ManualEditorSave,type ManualSaveTicket} from "./manualSave";
import {createEditorDocument} from "../editor/core/editorDocument";
import type {EditorCheckpoint} from "./editorSaveContracts";
import { PersistentEditor, type PersistentEditorHandle } from "../editor/drafts/PersistentEditor";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Dialog from "@mui/material/Dialog";
import {V2Dialog} from "../../ui/V2Dialog";
import {WorkspaceHeader} from "./WorkspaceHeader";
import {EditorBodySkeleton} from "./Skeletons";
import {useMediaQuery} from "../../ui/mui";
import "./editor-dialog.css";
import {useDialogDuration} from "./overlayTiming";
import { type EditorImageSavePayload, type EditorLocalGeneratePayload, type EditorLocalReferenceOption } from "../editor/EditorWorkspace";
import { normalizeEditRecipe, type EditRecipe } from "../editor/deformation/recipe";
import { activeStates, designApi, DesignApiError, type Revision } from "./api";
import { ReferenceUploadFailure } from "../references/attachments";
import { LocalEditorOperation, type LocalTicket } from "./localOperation";
import { EditorRoots } from "./editorRoots";
import { toast } from "../../ui/toastStore";
export type EditorDialogHandle={prepareLeave:()=>Promise<void>};
const EditorDialog=forwardRef<EditorDialogHandle, EditorDialogProps>(function EditorDialog({ ownerId, designId, cloudDraftId, revision, branchId, branchVersion, historical,
  canLocalGenerate = false, busy = false, newResultAvailable = false, references = [], maxInputImages = 8, onClose, onSaved, onSubmitted, onSignIn, closing = false },ref) {
  const {c,message}=useCloudEditorCopy();
  const dialogDuration=useDialogDuration();
  const phone=useMediaQuery("(max-width: 767px)",false);
  const [error, setError] = useState<unknown>("");
  const [saving, setSaving] = useState(false);
  const [localPreparing, setLocalPreparing] = useState(false);
  const [quoteVisible, setQuoteVisible] = useState(true);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [ticket, setTicket] = useState<LocalTicket | null>(null);
  const [recoveryFailed, setRecoveryFailed] = useState(false);
  // A local generation replaces the working image (undoably) instead of creating a version, so the root can change.
  const [roots] = useState(() => new EditorRoots({ownerId,branchId,revisionId:revision.id,baseRootAssetId:revision.root_source_asset_id}));
  const [rootAssetId, setRootAssetId] = useState(() => roots.current());
  const adopted = rootAssetId !== revision.root_source_asset_id;
  const rootUrl = adopted ? "/api/v2/assets/" + encodeURIComponent(rootAssetId) : revision.root_image_url;
  const local = useMemo(() => new LocalEditorOperation({ownerId,branchId,baseRevisionId:revision.id,
    rootAssetId,branchVersion,historical}), [ownerId,branchId,revision.id,rootAssetId]);
  const manual = useMemo(() => new ManualEditorSave({ownerId,branchId,baseRevisionId:revision.id,
    rootAssetId,branchVersion,historical}), [ownerId,branchId,revision.id,rootAssetId]);
  const adopting = useRef(false);
  const [manualTicket,setManualTicket]=useState<ManualSaveTicket|null>(null);
  const [manualNeedsLogin,setManualNeedsLogin]=useState(false);
  const manualFlight=useRef(false);
  const [checkpoint,setCheckpoint]=useState<EditorCheckpoint|null>();
  const [checkpointError,setCheckpointError]=useState(false);
  const [checkpointVerified,setCheckpointVerified]=useState(false);
  const live = useRef(true);
  const request = useRef<AbortController | null>(null);
  const localFlight = useRef(false);
  useEffect(() => {
    live.current = true; request.current = new AbortController();
    try { setTicket(local.load()); }
    catch { setRecoveryFailed(true); setError("cloud_editor_local_recovery_failed"); }
    try {setManualTicket(manual.load());} catch {setRecoveryFailed(true);setError("cloud_editor_manual_recovery_failed");}
    void readCheckpoint();
    return () => { live.current = false; request.current?.abort(); };
  }, [local, manual]);
  const receipt = ticket?.phase === "submitted" ? ticket : null;
  const result = useQuery({
    queryKey:["v2",ownerId,"local-result",designId,receipt?.branchId,receipt?.operationId],
    queryFn:()=>designApi.snapshot(designId,receipt!.branchId),
    enabled:Boolean(receipt), retry:false,
    refetchInterval:query => {
      const operation = query.state.data?.operations.find(item=>item.id===receipt?.operationId);
      return !operation || activeStates.has(operation.state) ? 2500 : false;
    },
  });
  const operation = result.data?.operations.find(item=>item.id===receipt?.operationId);
  const generating = localPreparing || Boolean(receipt && (!operation || activeStates.has(operation.state)));
  const editor = useRef<PersistentEditorHandle>(null);
  useImperativeHandle(ref,()=>({prepareLeave:async()=>{await editor.current?.flush();}}),[]);
  const initialRecipe = useRef(revision.recipe ? normalizeEditRecipe(revision.recipe as EditRecipe) : undefined);
  async function readCheckpoint(){
    setCheckpointError(false);
    const lifetime=request.current!.signal;
    const signal=AbortSignal.any([lifetime,AbortSignal.timeout(15000)]);
    try{
      const value=await designApi.editorCheckpoint(branchId,revision.id,signal);
      if(!live.current||signal.aborted)return;
      if(value.checkpoint&&(value.checkpoint.revision_id!==revision.id||value.checkpoint.document.root.assetId!==revision.root_source_asset_id))
        throw new Error("Checkpoint scope mismatch");
      setCheckpoint(value.checkpoint);setCheckpointVerified(true);
    }catch{if(live.current&&!lifetime.aborted)setCheckpointError(true);}
  }
  async function completeManual(){
    const saved=await manual.submit(request.current!.signal);
    if(!saved||!live.current)return;
    setManualTicket(manual.ticket);
    await editor.current?.clearSavedDocument(manual.ticket!.payload.document);
    if(!live.current)return;
    await onSaved(saved.checkpoint.branch_id);
    roots.reset();
    manual.acknowledge();
    if(live.current){setManualTicket(null);onClose();}
  }
  function saveFailure(failure:unknown){
    if(!live.current)return;
    setManualTicket(manual.ticket);setError(failure||"request_failed");
    if(failure instanceof DesignApiError&&failure.status===401)setManualNeedsLogin(true);
  }
  async function retryManual(){
    if(manualFlight.current||localFlight.current||!manualTicket)return;
    manualFlight.current=true;setSaving(true);setError("");setManualNeedsLogin(false);
    try{await completeManual();}catch(failure){saveFailure(failure);}
    finally{manualFlight.current=false;if(live.current)setSaving(false);}
  }
  async function save(payload: EditorImageSavePayload) {
    if(manualFlight.current||saving)return;
    if(manualTicket){setError("cloud_editor_manual_unconfirmed");return;}
    if(recoveryFailed||!checkpointVerified){setError("cloud_editor_save_unverified");return;}
    if(ticket||localFlight.current||busy){setError("cloud_editor_request_busy");return;}
    manualFlight.current=true;setSaving(true);setError("");setManualNeedsLogin(false);
    try{
      if(!payload.draftSnapshot)throw new Error("Frozen editor state missing");
      const source=editor.current!.source();
      const document=await createEditorDocument(payload.draftSnapshot,{assetId:rootAssetId,...source},revision.id,adopted?undefined:checkpoint?.document);
      await editor.current!.flush();
      request.current!.signal.throwIfAborted();
      await manual.prepare(document,payload.imageBlob,checkpoint?.id??null,request.current!.signal,unchangedEditorImage(revision,document));
      if(!live.current)return;
      setManualTicket(manual.ticket);
      await completeManual();
    }catch(failure){saveFailure(failure);}
    finally{manualFlight.current=false;if(live.current)setSaving(false);}
  }
  async function prepareLocal(payload: EditorLocalGeneratePayload) {
    if (localFlight.current || manualFlight.current || manualTicket || saving || busy || !canLocalGenerate || recoveryFailed || ticket) return;
    localFlight.current = true; setLocalPreparing(true); setError("");
    try {
      const source = editor.current!.source();
      await editor.current!.flush();
      request.current!.signal.throwIfAborted();
      const next = await local.prepare(payload,source,references,request.current!.signal,item=>{
        if(live.current && item.file && item.assetId) editor.current?.markReferenceUploaded(item.key,item.file,item.assetId);
      });
      if (live.current) { setTicket(next); setQuoteVisible(false); await submitLocal(); }
    } catch (failure) {
      if(live.current){
        if(failure instanceof ReferenceUploadFailure) editor.current?.markReferenceUploadFailure(failure.key,failure.file);
        setError((failure instanceof ReferenceUploadFailure ? failure.original : failure)||"request_failed");
      }
    }
    finally { localFlight.current = false; if (live.current) setLocalPreparing(false); }
  }
  // Pressing the editor's generate button is the consent; the quote dialog only reappears to retry or sign in.
  async function submitLocal() {
    setError(""); setNeedsLogin(false);
    try {
      const next = await local.submit(request.current!.signal);
      if (!live.current) return;
      setTicket(next);
      if (next?.phase === "submitted") await onSubmitted();
    } catch (failure) {
      if (live.current) { setTicket(local.ticket); setError(failure||"request_failed"); setQuoteVisible(true);
        if (failure instanceof DesignApiError && failure.status === 401) setNeedsLogin(true);
      }
    }
  }
  async function confirmLocal() {
    if (localFlight.current || ticket?.phase !== "quoted") return;
    localFlight.current = true; setLocalPreparing(true);
    try { await submitLocal(); }
    finally { localFlight.current = false; if (live.current) setLocalPreparing(false); }
  }
  function discardQuote() {
    try { if (local.discardQuote()) { setTicket(null); setError(""); } }
    catch { setError("cloud_editor_request_record_failed"); }
  }
  function hideQuote() {
    if (localFlight.current) return;
    if (ticket?.phase === "quoted" && ticket.attempted) setQuoteVisible(false);
    else discardQuote();
  }
  // Closing never loses anything: the draft, the adopted result and a running generation are all kept for the next visit.
  async function close() {
    if (saving || localFlight.current) return;
    try {
      await editor.current?.flush();
      if (live.current) onClose();
    } catch { if (live.current) setError("cloud_editor_leave_record_failed"); }
  }
  const [, bump] = useState(0);
  // A finished local generation replaces the working image; a failed one just reports and frees the controls again.
  useEffect(() => {
    if (!receipt || !operation || adopting.current || activeStates.has(operation.state)) return;
    adopting.current = true;
    void (async () => {
      try {
        if (operation.state === "succeeded" && operation.result_asset_id) {
          await editor.current?.flush(); // the work on the previous image stays available through undo
          if (!live.current) return;
          roots.adopt(operation.result_asset_id);
          if (local.acknowledgeFinished(operation)) setTicket(null);
          setRootAssetId(roots.current()); bump(value => value + 1);
          toast(c("adoptedResult"));
        } else {
          if (!local.clearFinished(operation)) local.acknowledgeFinished(operation);
          setTicket(null);
          setError(operation.error_code || "cloud_editor_local_no_image");
        }
      } finally { adopting.current = false; }
    })();
  }, [receipt?.operationId, operation?.state, operation?.result_asset_id]);
  async function stepRoot(direction: "undo" | "redo") {
    if (generating || saving) return;
    try {
      await editor.current?.flush(); // keep the current image's own draft before leaving it
      if (!live.current) return;
      if (direction === "undo") roots.undo(); else roots.redo();
      setRootAssetId(roots.current()); bump(value => value + 1);
    } catch { if (live.current) setError("cloud_editor_leave_record_failed"); }
  }
  function stopLocal() {
    if (!operation) return;
    void designApi.cancel(operation.id).then(() => result.refetch()).catch(failure => { if (live.current) setError(failure || "request_failed"); });
  }
  return <Dialog fullScreen={phone} maxWidth={false} open={!closing} transitionDuration={typeof dialogDuration==="number"?dialogDuration:{enter:0,exit:dialogDuration.exit}} className={"v2-workspace v2-workspace-editor"+(closing?" is-closing":"")} onClose={()=>void close()} aria-labelledby="v2-editor-title">
    <div className="v2-editor">
      <WorkspaceHeader dialog titleId="v2-editor-title" title={revision.number!==undefined?c("editVersion",{number:revision.number}):c("edit")}
        backLabel={c("backConversation")} onBack={()=>void close()} backDisabled={saving || localPreparing}
        secondary={<>{saving ? <span role="status">{c("saving")}</span> : null}
          {checkpoint!==undefined?<ProjectExportButton disabled={saving} capture={()=>editor.current!.captureProject()} name="KigCraft.png" resolveAsset={cloudDraftApi.asset}/>:null}</>}
        primary={<button type="button" className="v2-primary" disabled={saving || localPreparing || checkpoint===undefined} onClick={()=>void editor.current?.requestSave()}>{c("saveAndReturn")}</button>} />
      {newResultAvailable?<p role="status" data-testid="editor-new-result">{c("newResultAvailable")}</p>:null}
      <ErrorToast message={error ? message(error) : null} />
      {ticket?.phase === "quoted" && !quoteVisible && !localPreparing ? <p role="status">{c("submitUnconfirmed")}
        <button onClick={()=>setQuoteVisible(true)}>{c("resumeConfirm")}</button></p> : null}
      {generating ? <div className="v2-local-busy" role="status"><span className="kc-spinner" aria-hidden="true" />
        <span>{c("localGenerating")}</span>
        {operation && activeStates.has(operation.state) ? <button type="button" disabled={operation.state === "cancelling"} onClick={stopLocal}>{c("stopLocal")}</button> : null}</div> : null}
      {receipt && result.error && !operation ? <p className="v2-status">{c("resultUnavailable")}
        <button type="button" onClick={()=>void result.refetch()}>{c("retryRead")}</button></p> : null}
      {adopted || roots.canRedo() ? <div className="v2-local-adopted" role="status">
        <span>{adopted ? c("adoptedResult") : ""}</span>
        <button type="button" disabled={!adopted || generating || saving} onClick={()=>void stepRoot("undo")}>{c("undoLocal")}</button>
        <button type="button" disabled={!roots.canRedo() || generating || saving} onClick={()=>void stepRoot("redo")}>{c("redoLocal")}</button>
      </div> : null}
      {manualTicket?<p role="status">{c("pendingSave")}
        <button disabled={saving} onClick={()=>manualNeedsLogin&&onSignIn?onSignIn(retryManual):void retryManual()}>
          {c(manualNeedsLogin?"loginRetrySave":manualTicket.phase==="confirmed"?"finishSave":"retrySave")}</button>
        {manualTicket.phase==="prepared"&&!manualTicket.attempted?<button onClick={()=>{
          try{if(manual.discardUnattempted())setManualTicket(null);}catch{setError("cloud_editor_save_record_failed");}
        }}>{c("backToEdit")}</button>:null}
      </p>:null}
      {checkpoint===undefined&&!checkpointError?<EditorBodySkeleton/>:null}
      <ErrorToast message={checkpoint===undefined&&checkpointError?c("checkpointUnavailable"):null} action={{label:c("retryState"),onClick:()=>void readCheckpoint()}}/>
      {checkpoint===undefined&&checkpointError?<p className="v2-status"><button onClick={()=>setCheckpoint(null)}>{c("editOnDevice")}</button></p>:null}
      {checkpoint!==undefined?<div inert={saving || generating ? true : undefined}>
        <PersistentEditor cloudDraftId={cloudDraftId} savedDocument={adopted ? undefined : checkpoint?.document} cloudSync={Boolean(cloudDraftId)} ref={editor} key={ownerId + ":" + branchId + ":" + revision.id + ":" + rootAssetId} candidateIndex={1}
          sourceUrl={rootUrl}
          scope={{ ownerId, branchId, baseRevisionId: revision.id, rootAssetId }}
          recipe={adopted ? undefined : initialRecipe.current} imageCoordinateEditing downloadOnSave={false} showRegenerateActions={false}
          availableTools={editorViewTools(revision.view,canLocalGenerate)} initialLiquifySymmetry={revision.view==="front"}
          onUploadReference={file=>designApi.upload(file,ownerId,request.current?.signal)}
          localEditModes localReferenceOptions={references} localReferenceLimit={Math.max(0,maxInputImages-2)} onLocalGenerate={prepareLocal}
          isRegenerating={localPreparing || busy} localBusyLabel={generating ? c("localGenerating") : null} localGenerationDisabled={Boolean(ticket) || Boolean(manualTicket) || recoveryFailed}
          onSave={save} />
      </div>:null}
      <V2Dialog open={ticket?.phase === "quoted" && quoteVisible && !localPreparing} size="md" title={c("confirmLocal")} onClose={hideQuote}
        actions={ticket?.phase === "quoted" ? <>
          <button disabled={localPreparing} onClick={hideQuote}>{c(ticket.attempted?"backEditor":"backToEdit")}</button>
          <button className="v2-primary" disabled={localPreparing} onClick={()=>needsLogin && onSignIn ? onSignIn(confirmLocal) : void confirmLocal()}>
            {c(localPreparing?"sending":needsLogin?"loginContinue":ticket.attempted?"retrySend":"send")}</button>
        </> : undefined}>
        {ticket?.phase === "quoted" ? <>
          <p>{c("localCost",{units:ticket.units})}</p>
          <p>{c(ticket.payload.local_edit_mode === "lock_outside"?"lockOutside":"preserveOthers")}</p>
          <RequestPreview value={{text:ticket.payload.text,
            base_image_url:ticket.payload.prepared_edit?"/api/v2/assets/"+encodeURIComponent(ticket.payload.prepared_edit.clean_asset_id):null,
            references:ticket.payload.attachments.map((item,index)=>({image_url:"/api/v2/assets/"+encodeURIComponent(item.asset_id),usage:item.usage,number:index+1}))}}/>
          <MessageContent parts={[{type:"annotations",annotations:numberedAnnotations(ticket.payload.prepared_edit?.recipe.annotations)}]}/>
        </> : null}
      </V2Dialog>
    </div>
  </Dialog>;
});
type EditorDialogProps={
 ownerId: string; designId: string; cloudDraftId?:string; revision: Revision; branchId: string; branchVersion: number; historical: boolean;
 canLocalGenerate?: boolean; busy?: boolean; newResultAvailable?: boolean; references?: EditorLocalReferenceOption[]; maxInputImages?: number;
 onSignIn?: (action: () => void | Promise<void>) => void;
 onClose: () => void; onSaved: (branchId?:string) => Promise<void>; onSubmitted: () => Promise<void>; closing?: boolean;
};
export default EditorDialog;
