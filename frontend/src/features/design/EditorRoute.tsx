import {useCloudEditorCopy} from "./useCloudEditorCopy";
import {useEffect,useRef,useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {useBlocker} from "react-router-dom";
import {V2Dialog} from "../../ui/V2Dialog";
import {EditorSkeleton} from "./Skeletons";
import EditorDialog,{type EditorDialogHandle} from "./EditorDialog";
import {designApi,type Revision,type Snapshot} from "./api";
import {revisionKey} from "./viewerRoute";
import {referenceOptions} from "./referenceOptions";

export default function EditorRoute({ownerId,snapshot,revisionId,cloudDraftId,invalid,snapshotUnavailable,canLocalGenerate,busy,maxInputImages,onClose,onSaved,onSubmitted,onSignIn,closing=false}:{
 ownerId:string;snapshot:Snapshot;revisionId:string;cloudDraftId?:string;invalid:boolean;snapshotUnavailable:boolean;canLocalGenerate:boolean;busy:boolean;maxInputImages?:number;
 onClose:()=>void;onSaved:(branch?:string)=>Promise<void>;onSubmitted:()=>Promise<void>;
 onSignIn:(action:()=>void|Promise<void>)=>void;closing?:boolean;
}){
 const {c,message}=useCloudEditorCopy();
 const editor=useRef<EditorDialogHandle>(null),leaving=useRef(false),navigationPending=useRef(false);
 const [leaveError,setLeaveError]=useState(false),[attempt,setAttempt]=useState(0);
 const [target,setTarget]=useState<{revision:Revision;version:number;historical:boolean;currentAtOpen:string|null}|null>(null);
 const query=useQuery({
  queryKey:revisionKey(ownerId,snapshot.id,revisionId),
  queryFn:({signal})=>designApi.revision(snapshot.id,revisionId,signal),
  initialData:()=>snapshot.revisions.find(item=>item.id===revisionId)??(snapshot.current_revision?.id===revisionId?snapshot.current_revision:undefined),
  enabled:!invalid,retry:false,staleTime:Infinity,
 });
 useEffect(()=>{
  if(!target&&query.data&&!query.isError&&!invalid&&!snapshotUnavailable&&!snapshot.archived_at)
   setTarget({revision:query.data,version:snapshot.branch.state_version,currentAtOpen:snapshot.branch.current_revision_id,historical:query.data.id!==snapshot.branch.current_revision_id});
 },[target,query.data,query.isError,invalid,snapshotUnavailable,snapshot.archived_at,snapshot.branch.state_version,snapshot.branch.current_revision_id]);
 const blocker=useBlocker(({currentLocation,nextLocation})=>{
  const block=!leaving.current&&Boolean(target)&&
   (currentLocation.pathname!==nextLocation.pathname||currentLocation.search!==nextLocation.search);
  if(block)navigationPending.current=true;
  return block;
 });
 useEffect(()=>{
  if(blocker.state!=="blocked")return;
  let live=true;setLeaveError(false);
  void Promise.resolve(editor.current?.prepareLeave()).then(()=>{
   if(live){leaving.current=true;blocker.proceed();}
  }).catch(()=>{if(live)setLeaveError(true);});
  return()=>{live=false;};
 },[blocker.state,blocker.location?.key,attempt]);
 function close(){if(leaving.current||navigationPending.current)return;leaving.current=true;onClose();}
 async function saved(branch?:string){
  if(leaving.current||navigationPending.current)return;
  leaving.current=true;
  try{await onSaved(branch);}catch(error){leaving.current=false;throw error;}
 }
 const unavailable=invalid||(!target&&(snapshot.archived_at||query.isError||snapshotUnavailable))||(target?.historical&&!target.revision.can_continue);
 if(!target&&!unavailable)return <EditorSkeleton/>;
 if(!target||unavailable)return <V2Dialog open={!closing} title={c("editImage")} onClose={close}
  actions={<>{query.isError?<button onClick={()=>void query.refetch()}>{c("retryImages")}</button>:null}
   <button onClick={close}>{c("backConversation")}</button></>}>
  <p role={unavailable?"alert":"status"}>{invalid?c("invalidLocation"):snapshot.archived_at?c("archived"):
   query.isError?message(query.error):snapshotUnavailable?c("stateUnavailable"):target?c("contextUnavailable"):c("restoring")}</p>
 </V2Dialog>;
 const newResultAvailable=!snapshotUnavailable&&Boolean(snapshot.branch.current_revision_id)&&
  snapshot.branch.current_revision_id!==target.currentAtOpen&&snapshot.branch.current_revision_id!==target.revision.id;
 return <>
  <EditorDialog newResultAvailable={newResultAvailable} cloudDraftId={cloudDraftId} ref={editor} ownerId={ownerId} designId={snapshot.id} revision={target.revision} branchId={snapshot.branch.id}
   branchVersion={target.version} historical={target.historical} busy={busy||snapshotUnavailable||Boolean(snapshot.archived_at)} canLocalGenerate={canLocalGenerate&&!snapshotUnavailable}
   references={referenceOptions(snapshot)} maxInputImages={maxInputImages} onSignIn={onSignIn}
   onClose={close} onSaved={saved} onSubmitted={onSubmitted} closing={closing}/>
  <V2Dialog open={blocker.state==="blocked"} dismissible={false} title={c("saveDraft")} onClose={()=>{}}
   actions={leaveError?<><button onClick={()=>setAttempt(value=>value+1)}>{c("retryLeave")}</button>
     <button onClick={()=>{if(blocker.state==="blocked"){navigationPending.current=false;blocker.reset();}}}>{c("keepEditing")}</button></>:undefined}>
   <p role={leaveError?"alert":"status"}>{c(leaveError?"leaveFailed":"savingDraft")}</p>
  </V2Dialog>
 </>;
}
