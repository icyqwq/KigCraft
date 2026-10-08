import {useEffect,useRef,useState} from "react";
import {discardSavedComposerDrafts,emptyComposerDraft,type ComposerDraft} from "./composerDraft";

/**
 * The composer's working state: text, attachments, the chosen base image and an unconfirmed request.
 * It lives in memory only. Nothing is saved between visits, so there is nothing to restore, merge or choose between.
 */
export function useComposerDraft(){
  const [draft,setDraft]=useState(()=>emptyComposerDraft());
  const current=useRef(draft);
  const live=useRef(false);
  useEffect(()=>{discardSavedComposerDrafts();live.current=true;return()=>{live.current=false;};},[]);
  function apply(update:ComposerDraft|((draft:ComposerDraft)=>ComposerDraft)){
    const next=typeof update==="function"?update(current.current):update;
    current.current=next;setDraft(next);
  }
  return {draft,current,live,
    update:apply,
    change:async(update:ComposerDraft|((draft:ComposerDraft)=>ComposerDraft))=>{apply(update);},
    commit:async(update:ComposerDraft|((draft:ComposerDraft)=>ComposerDraft))=>{if(live.current)apply(update);}};
}
