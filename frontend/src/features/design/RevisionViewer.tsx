import {useViewerCopy} from "./useViewerCopy";
import {ErrorToast} from "../../ui/ErrorToast";
import {useLayoutEffect,useMemo,useRef} from "react";
import {handOverSharedElement} from "./transitions";
import {useDialogDuration} from "./overlayTiming";
import {useInfiniteQuery} from "@tanstack/react-query";
import Dialog from "@mui/material/Dialog";
import {IconChevronLeft,IconChevronRight,IconX} from "@tabler/icons-react";
import {designApi,errorText,type Revision} from "./api";

export default function RevisionViewer({ownerId,designId,revision,selected,comparison,allVersions,canContinue,onChange,onContinue,onClose,closing=false}:{
 ownerId:string;designId:string;revision:Revision;selected:Revision;comparison:Revision|null;allVersions:boolean;canContinue:boolean;
 onChange:(selected:Revision,comparison:Revision|null,allVersions:boolean)=>void;
 onContinue:(revision:Revision)=>void;onClose:()=>void;closing?:boolean;
}){
 const {c,language}=useViewerCopy();
 const dialogDuration=useDialogDuration();
 const strip=useRef<HTMLDivElement>(null),mainImage=useRef<HTMLImageElement>(null);
 useLayoutEffect(()=>{
  const image=mainImage.current;
  return image?handOverSharedElement(selected.id,image):undefined;
 },[selected.id]);
 const reveal=useRef({key:"",done:false});
 const revealKey=JSON.stringify([selected.id,allVersions]);
 if(reveal.current.key!==revealKey)reveal.current={key:revealKey,done:false};
 // Consecutive controls can fire before the router commits the previous selection.
 const selection=useRef({selected,comparison,allVersions});
 useLayoutEffect(()=>{selection.current={selected,comparison,allVersions};},[selected,comparison,allVersions]);
 function updateSelection(next:typeof selection.current){
  selection.current=next;onChange(next.selected,next.comparison,next.allVersions);
 }
 const setComparison=(value:Revision|null)=>updateSelection({...selection.current,comparison:value});
 const setAllVersions=(value:boolean)=>updateSelection({...selection.current,allVersions:value});
 const candidates=useInfiniteQuery({
  queryKey:["v2",ownerId,"candidates",designId,revision.id],
  queryFn:({pageParam,signal})=>designApi.candidates(revision.id,pageParam,signal),
  initialPageParam:null as string|null,getNextPageParam:page=>page.next_cursor??undefined,
  enabled:!allVersions,retry:false,staleTime:Infinity,gcTime:0,
 });
 const history=useInfiniteQuery({
  queryKey:["v2",ownerId,"revisions",designId],
  queryFn:({pageParam,signal})=>designApi.revisions(designId,pageParam,signal),
  initialPageParam:null as string|null,getNextPageParam:page=>page.next_cursor??undefined,
  enabled:allVersions,retry:false,staleTime:Infinity,gcTime:0,
 });
 const query=allVersions?history:candidates;
 const items=useMemo(()=>{
  const values=new Map<string,Revision>([[revision.id,revision]]);
  for(const page of query.data?.pages??[])for(const item of page.items)values.set(item.id,item);
  if(allVersions){values.set(selected.id,selected);if(comparison)values.set(comparison.id,comparison);}
  return [...values.values()].sort((a,b)=>(a.history_sequence??a.number??0)-(b.history_sequence??b.number??0));
 },[query.data,revision,allVersions,selected,comparison]);
 const index=items.findIndex(item=>item.id===selected.id);
 const total=query.data?.pages[0].total??1;
 const options=comparison&&!items.some(item=>item.id===comparison.id)?[comparison,...items]:items;
 function choose(item:Revision){
  const current=selection.current;
  updateSelection({...current,selected:item,comparison:item.id===current.comparison?.id?current.selected:current.comparison});
 }
 function move(direction:number){
  const index=items.findIndex(item=>item.id===selection.current.selected.id),next=items[index+direction];
  if(index>=0&&next)choose(next);
 }
 function toggleComparison(){
  const current=selection.current;
  setComparison(current.comparison?null:items.find(item=>item.id!==current.selected.id)??null);
 }
 function continueSelected(){
  const current=selection.current.selected;
  if(canContinue&&current.can_continue)onContinue(current);
 }
 useLayoutEffect(()=>{
  if(reveal.current.done||!query.data)return;
  const el=strip.current,node=el?.querySelector<HTMLElement>('button[aria-pressed="true"]');
  if(el&&node){
   el.scrollLeft+=node.getBoundingClientRect().left-el.getBoundingClientRect().left-(el.clientWidth-node.clientWidth)/2;
   reveal.current.done=true;
  }
 },[selected.id,allVersions,query.data]);
 const label=(item:Revision)=>item.number===undefined?c("image"):c("version",{number:item.number});
 return <Dialog open={!closing} className="v2-lightbox" transitionDuration={dialogDuration} onClose={onClose} maxWidth={false} aria-labelledby="v2-viewer-title">
  <div className="v2-viewer">
   <h2 id="v2-viewer-title" className="v2-sr-only">{label(selected)}</h2>
   <div className="v2-viewer-bar">
    <span className="v2-revision-number">{label(selected)}</span>
    <button className="v2-primary" disabled={!canContinue||!selected.can_continue} onClick={continueSelected}>{c("continue")}</button>
    <a href={selected.image_url} download="kigcraft.jpg">{c("download")}</a>
    <button className="v2-icon" aria-label={c("close")} onClick={onClose}><IconX size={18} stroke={1.75}/></button>
   </div>
   <div className={"v2-comparison"+(comparison?" is-comparing":"")}>
    {comparison?<figure><figcaption>{label(comparison)}</figcaption><img key={comparison.id} src={comparison.image_url} alt={c("comparisonAlt",{label:label(comparison)})}/></figure>:null}
    <figure>{comparison?<figcaption>{label(selected)}</figcaption>:null}<img ref={mainImage} key={selected.id} src={selected.image_url} alt={c("original")}/></figure>
   </div>
   <div className="v2-candidate-controls v2-version-controls">
    <button aria-pressed={!allVersions} onClick={()=>setAllVersions(false)}>{c("candidates")}</button>
    <button aria-pressed={allVersions} onClick={()=>setAllVersions(true)}>{c("versions")}</button>
    <button disabled={!comparison&&!items.some(item=>item.id!==selected.id)} aria-pressed={Boolean(comparison)}
      onClick={toggleComparison}>{c(comparison?"endCompare":"compare")}</button>
    {comparison?<select aria-label={c("comparison")} value={comparison.id} onChange={event=>setComparison(options.find(item=>item.id===event.target.value)??null)}>
     {options.filter(item=>item.id!==selected.id).map(item=><option key={item.id} value={item.id}>{label(item)}</option>)}
    </select>:null}
   </div>
   {total>1||allVersions?<section className="v2-candidates" aria-label={c(allVersions?"versions":"candidates")}>
    <div className="v2-candidate-controls">
     <button className="v2-icon" aria-label={c(allVersions?"previousVersion":"previousCandidate")} disabled={index<=0} onClick={()=>move(-1)}><IconChevronLeft size={18} stroke={1.75}/></button>
     <span>{c(allVersions?"versionCount":"candidateCount",{count:query.hasNextPage?items.length+" / "+total:total})}</span>
     <button className="v2-icon" aria-label={c(allVersions?"nextVersion":"nextCandidate")} disabled={index<0||index>=items.length-1} onClick={()=>move(1)}><IconChevronRight size={18} stroke={1.75}/></button>
    </div>
    <div ref={strip} className="v2-candidate-strip">
     {items.map(item=><button key={item.id} aria-label={c(allVersions?"viewVersion":"viewCandidate",{label:label(item)})} aria-pressed={selected.id===item.id}
       onClick={()=>choose(item)}><img src={item.image_url} alt="" loading="lazy"/><span>{label(item)}</span></button>)}
    </div>
   </section>:null}
   {query.isLoading?<p role="status">{c(allVersions?"loadingVersions":"loadingCandidates")}</p>:null}
   <ErrorToast message={query.isError?errorText(query.error,language):null}
    action={{label:c(allVersions?"retryVersions":"retryCandidates"),onClick:()=>void(query.isFetchNextPageError?query.fetchNextPage():query.refetch())}}/>
   {query.hasNextPage?<button disabled={query.isFetching} onClick={()=>void query.fetchNextPage()}>{c(query.isFetchingNextPage?"loading":allVersions?"moreVersions":"moreCandidates")}</button>:null}
  </div>
 </Dialog>;
}
