import type {HealthReport} from "./api";
import {useRecoveryCopy} from "./useRecoveryCopy";
const names={
 expired_leases:"healthLeases",publishing_backlog:"healthPublishing",unresolved_operations:"healthUnresolved",
 wallet_mismatch:"healthWallet",review_failures:"healthReview",storage_capacity:"healthStorage",
 runtime_identity:"healthIdentity",duplicate_outputs:"healthDuplicates",access_denials:"healthAccess",
} as const;

export function HealthSignals({value,onOperation}:{value?:HealthReport;onOperation:(id:string)=>void}){
 const {c,date,language}=useRecoveryCopy(),number=new Intl.NumberFormat(language);
 return <section aria-label={c("healthTitle")}><h2>{c("healthTitle")}</h2>
  <p>{c("healthScope")}</p>
  {!value?<p>{c("unknown")}</p>:<>
   <p>{c("healthChecked",{date:date(value.checked_at),seconds:number.format(value.stalled_seconds),window:number.format(value.recent_seconds)})}</p>
   <ul>{value.signals.map(s=><li key={s.code}>
    <strong>{c(names[s.code])}</strong>{" · "}{c(s.status==="active"?"healthActive":s.status==="clear"?"healthClear":"unknown")}
    {s.count!==null?" · "+c("healthCount",{count:number.format(s.count),threshold:number.format(s.threshold)}):null}
    {s.code==="storage_capacity"&&s.scope!=="minio"&&s.free_bytes!=null?<span>{" · "+c("healthDisk",{free:number.format(Math.floor(s.free_bytes/1024/1024)),minimum:number.format(Math.floor((s.minimum_free_bytes??0)/1024/1024))})}</span>:null}
    {s.scope==="minio"&&s.capacity?<ul>{(["cluster","bucket"] as const).map(key=>{
     const part=s.capacity![key];
     return <li key={key}>{c(key==="cluster"?"healthCluster":"healthBucket")}{": "}
      {c(part.status==="active"?"healthActive":part.status==="clear"?"healthClear":"unknown")}
      {part.free_bytes!=null?" · "+c("healthDisk",{free:number.format(Math.floor(part.free_bytes/1024/1024)),
       minimum:number.format(Math.floor((s.minimum_free_bytes??0)/1024/1024))}):null}
      {key==="bucket"&&s.capacity!.bucket.unlimited?" · "+c("healthUnlimited"):null}
     </li>;
    })}</ul>:null}
    {s.coverage?<p>{c(s.coverage.scope==="private_resource_rejections"?"healthAccessPrivateScope":s.coverage.scope==="authenticated_v2_resource_references"?"healthAccessReferenceScope":"healthAccessScope")} · {c(s.coverage.status==="complete"?"healthCollectionComplete":"healthCollectionIncomplete")}
     {" · "+c("healthCollectors",{active:number.format(s.coverage.active_processes),expected:number.format(s.coverage.expected_processes)})}
     {s.coverage.gap_recorded?" · "+c("healthCollectionGap"):null}
    </p>:null}
    {s.categories?<p>{c("healthAccessRecorded",{unauthenticated:number.format(s.categories.unauthenticated),
     forbidden:number.format(s.categories.forbidden),closed:number.format(s.categories.stream_closed)})}</p>:null}
    {s.operation_ids.length?<ul>{s.operation_ids.map(id=><li key={id}><button onClick={()=>onOperation(id)}>{c("healthOpen",{id})}</button></li>)}</ul>:null}
   </li>)}</ul>
  </>}
 </section>;
}
