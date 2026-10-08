import {Fragment} from "react";
import type {RuntimeUsage,RuntimeUsageTotals} from "./api";
import {useRecoveryCopy} from "./useRecoveryCopy";
import type copy from "./copy-zh-CN";
type Key=keyof typeof copy;
const states:Record<string,Key>={dispatch_intent:"usageIntent",started:"usageStarted",completed:"usageCompleted",interrupted:"usageInterrupted",failed:"usageFailed"};
const totals:[keyof Omit<RuntimeUsageTotals,"turn_tokens"|"provider_model_requests"|"amount">,Key][]=[
 ["dispatch_intents","usageDispatches"],["acknowledged_attempts","usageAcknowledged"],["reported_snapshots","usageReported"],
 ["missing_snapshots","usageMissing"],["invalid_snapshots","usageInvalid"],["operations_without_observations","usageUntracked"],
];
const tokens=[["inputTokens","usageInput"],["cachedInputTokens","usageCached"],["cacheWriteInputTokens","usageCacheWrite"],
 ["outputTokens","usageOutput"],["reasoningOutputTokens","usageReasoning"],["totalTokens","usageTokens"]] as const;
export function UsageCoverage({value}:{value?:RuntimeUsageTotals}){
 const {c,language}=useRecoveryCopy(),format=new Intl.NumberFormat(language);
 return <section aria-label={c("usageCoverage")}><h2>{c("usageCoverage")}</h2>
  {value?<dl className="v2-usage-metrics">{totals.map(([field,label])=><Fragment key={field}><dt>{c(label)}</dt><dd>{format.format(value[field])}</dd></Fragment>)}</dl>:<p>{c("usageUnavailable")}</p>}
  <p>{c("usageNote")}</p>
 </section>;
}
export function UsageDetails({value}:{value?:RuntimeUsage}){
 const {c,language,date}=useRecoveryCopy(),format=new Intl.NumberFormat(language);
 return <div role="region" aria-label={c("usageTitle")} className="v2-usage">
  <h3>{c("usageTitle")}</h3>
  {!value?.attempts.length?<p>{c("usageUnavailable")}</p>:<ol className="v2-usage-attempts">{value.attempts.map((attempt,index)=><li key={index}>
   <h4>{c(attempt.phase==="review"?"usageReview":"usageGeneration")} · {attempt.requested_model}</h4>
   <p>{c(states[attempt.state])}</p>
   <p>{c("usageImageCount",{count:format.format(attempt.observed_image_items)})} · {c(attempt.image_coverage==="partial"?"usagePartial":"usageTerminal")}</p>
   <p>{c("usageUpdated",{date:date(attempt.updated_at)})}</p>
   <p>{attempt.image_anomalies?c("usageAnomalies",{replays:format.format(attempt.image_anomalies.replayed_items),
    conflicts:format.format(attempt.image_anomalies.conflicting_items),extra:format.format(attempt.image_anomalies.extra_completed_items)}):c("usageAnomaliesUnknown")}</p>
   {attempt.token_status!=="observed"||!attempt.token_snapshot?<p>{c(attempt.token_status==="invalid"?"usageBadTokens":"usageNoTokens")}</p>:
    <details><summary>{c("usageSnapshot")}</summary><p>{c("usageNote")}</p>
     <div className="v2-usage-snapshots">{(["last","total"] as const).map(scope=><div key={scope}>
      <h5>{c(scope==="last"?"usageLast":"usageTotal")}</h5>
      <dl className="v2-usage-metrics">{tokens.map(([field,label])=><Fragment key={field}><dt>{c(label)}</dt>
       <dd>{attempt.token_snapshot![scope][field]===null?c("unknown"):format.format(attempt.token_snapshot![scope][field]!)}</dd></Fragment>)}</dl>
     </div>)}</div>
     <p>{c("usageContext")} · {attempt.token_snapshot.modelContextWindow===null?c("unknown"):format.format(attempt.token_snapshot.modelContextWindow)}</p>
    </details>}
  </li>)}</ol>}
  <p>{c("usageImageNote")}</p>
 </div>;
}
