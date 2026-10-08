import {useEffect,useState} from "react";
import {ErrorToast} from "../../ui/ErrorToast";
import {useInfiniteQuery} from "@tanstack/react-query";
import {V2Dialog} from "../../ui/V2Dialog";
import {useAuth} from "../auth/AuthProvider";
import {readLegacySources} from "./legacyApi";
import {useLegacyCopy} from "./useLegacyCopy";
export default function LegacySources({revisionId,designId}:{revisionId?:string;designId?:string}){
 const {c,view,state,message}=useLegacyCopy();
 const identity=revisionId??designId!;
 const scope=revisionId?"revisions":"designs";
 const label=c(revisionId?"revisionTitle":"designTitle");
 const {currentUser,authSessionEpoch}=useAuth();
 const [open,setOpen]=useState(false);
 useEffect(()=>setOpen(false),[currentUser?.id,authSessionEpoch,identity,scope]);
 const query=useInfiniteQuery({
  queryKey:["v2",currentUser?.id,authSessionEpoch,"legacy-sources",scope,identity],
  queryFn:({pageParam,signal})=>readLegacySources(identity,pageParam,signal,scope),
  initialPageParam:null as string|null,getNextPageParam:page=>page.next_cursor??undefined,
  enabled:open&&Boolean(currentUser),retry:false,
 });
 const items=query.data?.pages.flatMap(page=>page.items)??[];
 return <>
  <button onClick={()=>setOpen(true)}>{label}</button>
  <V2Dialog open={open} size="md" labelId={"legacy-title-"+scope+"-"+identity} title={label} onClose={()=>setOpen(false)}
   actions={<button onClick={()=>setOpen(false)}>{c("close")}</button>}>
    <p>{c("preserved")}</p>
    {query.isLoading?<p role="status">{c("loading")}</p>:null}
    <ErrorToast message={query.isError?message(query.error):null} action={{label:c("retry"),onClick:()=>void(query.isFetchNextPageError?query.fetchNextPage():query.refetch())}}/>
    {items.map(item=><article key={item.id}>
     <h3>{c(item.source_kind)}</h3>
     <p>{c("imageSource",{provider:(item.provider.image_provider??item.provider.provider??c("notRecorded"))+(item.provider.image_model?" · "+item.provider.image_model:"")})}</p>
     {item.provider.analysis_provider?<p>{c("analysisSource",{provider:item.provider.analysis_provider+(item.provider.analysis_model?" · "+item.provider.analysis_model:"")})}</p>:null}
     <p>{view(item.view)} · {state(item.legacy_execution)}{item.legacy_selection==="confirmed"?" · "+c("selected"):""}</p>
     {item.recipe_state==="legacy_unverified"||item.recipe_state==="invalid"?<p>{c("recipeUnverified")}</p>:null}
     {item.issues.some(issue=>!issue.startsWith("recipe_"))?<p>{c("partial")}</p>:null}
    </article>)}
    {!query.isLoading&&!query.isError&&!items.length?<p>{c("empty")}</p>:null}
    {query.hasNextPage?<button disabled={query.isFetching} onClick={()=>void query.fetchNextPage()}>{c("more")}</button>:null}
  </V2Dialog>
 </>;
}
