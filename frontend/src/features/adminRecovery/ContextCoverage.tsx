import type {ContextRecovery} from "./api";
import {useRecoveryCopy} from "./useRecoveryCopy";

export function ContextCoverage({value}:{value?:ContextRecovery}){
 const {c,language,date}=useRecoveryCopy();
 const number=new Intl.NumberFormat(language),percent=new Intl.NumberFormat(language,{style:"percent",maximumFractionDigits:1});
 return <section aria-label={c("contextTitle")}><h2>{c("contextTitle")}</h2>
  {!value?<p>{c("unknown")}</p>:<>
   <p>{c("contextScope")}</p>
   <dl>{(["resume","fork","start"] as const).map(method=>{
    const item=value.methods[method];
    return <div key={method}><dt>{c(method==="resume"?"contextResume":method==="fork"?"contextFork":"contextStart")}</dt>
     <dd>{c("contextCounts",{confirmed:number.format(item.confirmed),attempts:number.format(item.attempts),
      unknown:number.format(item.unconfirmed),rate:item.confirmation_rate===null?c("unknown"):percent.format(item.confirmation_rate)})}</dd></div>;
   })}</dl>
   <p>{value.since?c("contextSince",{date:date(value.since)}):c("contextEmpty")}</p>
   <p>{c("contextMissing",{count:number.format(value.dispatched_operations_without_observations)})}</p>
  </>}
 </section>;
}
