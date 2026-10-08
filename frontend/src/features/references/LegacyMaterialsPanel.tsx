import {useInfiniteQuery} from "@tanstack/react-query";
import {ErrorToast} from "../../ui/ErrorToast";
import {z} from "zod";
import {useAuth} from "../auth/AuthProvider";
import {useReferenceCopy} from "./useReferenceCopy";
import type {ReferenceOption} from "./attachments";
import "./references.css";

const itemSchema=z.object({
 id:z.string(),source_kind:z.enum(["reference","project_reference","draft"]),
 file_name:z.string().nullable(),legacy_kind:z.string().nullable(),project_name:z.string().nullable(),
 asset_id:z.string().nullable(),width:z.number().nullable(),height:z.number().nullable(),
 draft_preserved:z.boolean(),editor_document:z.literal(false),
});
const pageSchema=z.object({items:z.array(itemSchema),next_cursor:z.string().nullable()});
async function load(kind:"reference"|"all",cursor:string|null,signal:AbortSignal){
 const query=new URLSearchParams({kind});if(cursor)query.set("cursor",cursor);
 const response=await fetch("/api/v2/account/legacy-materials?"+query,{credentials:"include",signal});
 if(!response.ok)throw new Error("legacy_materials_unavailable");
 return pageSchema.parse(await response.json());
}
export default function LegacyMaterialsPanel({ownerId,onChoose,selected=[],maximum=0,disabled=false}:{
 ownerId:string;onChoose?:(option:ReferenceOption)=>void;selected?:string[];maximum?:number;disabled?:boolean;
}){
 const c=useReferenceCopy(),auth=useAuth(),kind=onChoose?"reference":"all";
 const active=auth.currentUser?.id===ownerId;
 const query=useInfiniteQuery({queryKey:["v2",ownerId,auth.authSessionEpoch,"legacy-materials",kind],
  queryFn:({pageParam,signal})=>load(kind,pageParam,signal),initialPageParam:null as string|null,
  getNextPageParam:page=>page.next_cursor??undefined,enabled:active,retry:false});
 const items=[...new Map(query.data?.pages.flatMap(page=>page.items).map(item=>[item.id,item])??[]).values()];
 if(!active)return null;
 return <section aria-label={c("legacyTitle")}>
  {query.isPending?<p role="status">{c("legacyLoading")}</p>:null}
  <ErrorToast message={query.isError?c("legacyFailed"):null}
    action={{label:c("legacyRetry"),onClick:()=>void(query.isFetchNextPageError?query.fetchNextPage():query.refetch())}}/>
  {!query.isPending&&!query.isError&&!items.length?<p>{c("legacyEmpty")}</p>:null}
  <div className="legacy-materials-list">{items.map(item=>{
   const label=item.file_name??item.project_name??c(item.draft_preserved?"legacyDraft":"legacyReference");
   const chosen=!!item.asset_id&&selected.includes(item.asset_id);
   return <article key={item.id}>
    {item.asset_id?<img src={"/api/v2/assets/"+encodeURIComponent(item.asset_id)} alt="" loading="lazy"/>:null}
    <strong>{label}</strong>
    {item.project_name&&item.file_name?<span>{item.project_name}</span>:null}
    {item.draft_preserved?<><p>{c("legacyDraftNote")}</p>
      <a href={"/api/v2/account/legacy-materials/"+encodeURIComponent(item.id)+"/draft"} download>{c("legacyDownloadDraft")}</a></>
     :item.asset_id?onChoose?<button type="button" aria-pressed={chosen}
      disabled={disabled||(!chosen&&selected.length>=maximum)}
      onClick={()=>onChoose({key:item.asset_id!,label,imageUrl:"/api/v2/assets/"+encodeURIComponent(item.asset_id!)})}>
       {c(chosen?"legacySelected":"legacySelect")}</button>
      :<a href={"/api/v2/assets/"+encodeURIComponent(item.asset_id)} download>{c("legacyDownloadImage")}</a>
     :<p>{c("legacyUnavailable")}</p>}
   </article>;
  })}</div>
  {query.hasNextPage?<button type="button" disabled={query.isFetchingNextPage}
    onClick={()=>void query.fetchNextPage()}>{c(query.isFetchingNextPage?"legacyLoading":"legacyMore")}</button>:null}
 </section>;
}
