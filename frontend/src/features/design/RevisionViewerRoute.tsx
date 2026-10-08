import {useViewerCopy} from "./useViewerCopy";
import {ErrorToast} from "../../ui/ErrorToast";
import {useQueries} from "@tanstack/react-query";
import {V2Dialog} from "../../ui/V2Dialog";
import RevisionViewer from "./RevisionViewer";
import {designApi,errorText,type Revision} from "./api";
import {revisionKey,type ViewerLocation} from "./viewerRoute";

export default function RevisionViewerRoute({ownerId,designId,location,known,canContinue,onChange,onContinue,onClose,closing=false}:{
 ownerId:string;designId:string;location:ViewerLocation|null;known:Revision[];canContinue:boolean;
 onChange:(location:ViewerLocation,revisions:Revision[])=>void;onContinue:(revision:Revision)=>void;onClose:()=>void;closing?:boolean;
}){
 const {c,language}=useViewerCopy();
 const ids=location?[...new Set([location.origin,location.selected,...(location.comparison?[location.comparison]:[])])]:[];
 const queries=useQueries({queries:ids.map(id=>({
  queryKey:revisionKey(ownerId,designId,id),
  queryFn:({signal}:{signal:AbortSignal})=>designApi.revision(designId,id,signal),
  initialData:()=>known.find(item=>item.id===id),retry:false,staleTime:Infinity,
 }))});
 const error=queries.find(query=>query.isError)?.error;
 const loading=queries.some(query=>!query.data);
 if(!location||error||loading)return <V2Dialog open={!closing} title={c("viewer")} onClose={onClose}
  actions={<>{error?<button onClick={()=>{for(const query of queries)if(query.isError)void query.refetch();}}>{c("retryImages")}</button>:null}
   <button onClick={onClose}>{c("close")}</button></>}>
  <ErrorToast message={error||!location?(location?errorText(error,language):c("invalidLocation")):null}/>
  {error||!location?null:<div role="status" aria-busy="true" aria-label={c("restoring")} className="v2-skeleton v2-skeleton-image" style={{width:"100%"}}/>}
 </V2Dialog>;
 const values=queries.map(query=>query.data!);
 const get=(id:string)=>values.find(item=>item.id===id)!;
 return <RevisionViewer ownerId={ownerId} designId={designId} revision={get(location.origin)}
  selected={get(location.selected)} comparison={location.comparison?get(location.comparison):null}
  allVersions={location.allVersions} canContinue={canContinue} onContinue={onContinue} onClose={onClose} closing={closing}
  onChange={(selected,comparison,allVersions)=>onChange({...location,selected:selected.id,comparison:comparison?.id??null,allVersions},
   [selected,...(comparison?[comparison]:[])])}/>;
}
