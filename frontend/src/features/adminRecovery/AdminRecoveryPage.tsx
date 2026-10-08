import {useState} from "react";
import {useRecoveryCopy} from "./useRecoveryCopy";
import {LanguageSelector} from "../i18n/LanguageSelector";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import {Link} from "react-router-dom";
import {useAuth} from "../auth/AuthProvider";
import {adminRecovery,commandSchema,RecoveryError,type Command} from "./api";
import "./recovery.css";
import {UsageCoverage,UsageDetails} from "./RuntimeUsage";
import {ContextCoverage} from "./ContextCoverage";
import {HealthSignals} from "./HealthSignals";
export default function AdminRecoveryPage(){
 const {currentUser,authSessionEpoch}=useAuth();
 const {c}=useRecoveryCopy();
 if(!currentUser)return <main className="v2-recovery"><header><h1>{c("title")}</h1><LanguageSelector compact/></header><p>{c("signInRequired")}</p><Link to="/login">{c("signIn")}</Link></main>;
 if(currentUser.role!=="admin")return <main className="v2-recovery"><header><h1>{c("title")}</h1><LanguageSelector compact/></header><p role="alert">{c("adminRequired")}</p><Link to="/v2">{c("backDesign")}</Link></main>;
 return <Console key={currentUser.id+":"+authSessionEpoch} owner={currentUser.id} epoch={authSessionEpoch}/>;
}
function Console({owner,epoch}:{owner:string;epoch:number}){
 const {c,label,automatic,confirm,message,date}=useRecoveryCopy();
 const client=useQueryClient(),prefix=["v2-admin",owner,epoch];
 const [state,setState]=useState("reconciling"),[offset,setOffset]=useState(0),[auditOffset,setAuditOffset]=useState(0);
 const [selected,setSelected]=useState<string|null>(null),[form,setForm]=useState<{action:Command["action"];target_id:string}|null>(null);
 const [reason,setReason]=useState(""),[units,setUnits]=useState(1),[pending,setPending]=useState(false),[error,setError]=useState<unknown>(""),[result,setResult]=useState<{status:string;id:string}|null>(null);
 const storageKey="kig-v2-admin-request:"+owner;
 const [saved,setSaved]=useState<Command|null>(()=>{try{const raw=sessionStorage.getItem(storageKey);return raw?commandSchema.parse(JSON.parse(raw)):null;}catch{return null;}});
 const refresh=()=>void client.invalidateQueries({queryKey:prefix});
 const overview=useQuery({queryKey:[...prefix,"overview"],queryFn:adminRecovery.overview,retry:false,refetchInterval:5000});
 const operations=useQuery({queryKey:[...prefix,"operations",state,offset],queryFn:()=>adminRecovery.operations(state,offset),retry:false,refetchInterval:5000});
 const detail=useQuery({queryKey:[...prefix,"detail",selected],queryFn:()=>adminRecovery.detail(selected!),enabled:!!selected,retry:false,refetchInterval:5000});
 const actions=useQuery({queryKey:[...prefix,"actions",auditOffset],queryFn:()=>adminRecovery.actions(auditOffset),retry:false,refetchInterval:5000});
 const choose=(action:Command["action"],target_id="global")=>{setForm({action,target_id});setReason("");setUnits(1);setError("");setResult(null);};
 const execute=async(request:Command)=>{
  if(pending)return;
  setPending(true);setError("");setResult(null);
  try{
   sessionStorage.setItem(storageKey,JSON.stringify(request));setSaved(request);
  }catch{setError("recovery_storage_failed");setPending(false);return;}
  try{
   const returned=await adminRecovery.submit(request);
   try{sessionStorage.removeItem(storageKey);}catch{/* Replaying the retained ID remains safe. */}
   setSaved(null);setForm(null);setResult({status:returned.status,id:returned.id});refresh();
  }catch(e){setError(e||"request_failed");
   if(e instanceof RecoveryError&&[400,401,403,404,409,422].includes(e.status)){
    try{sessionStorage.removeItem(storageKey);}catch{/* Retain the request for explicit replay. */}
    setSaved(null);
   }
  }finally{setPending(false);}
 };
 const op=detail.data,paused=overview.data?.controls.global??false;
 const quarantined=op?overview.data?.controls["owner:"+op.owner_id]??false:false;
 return <main className="v2-recovery">
  <header><h1>{c("title")}</h1><nav><LanguageSelector compact/><Link to="/v2">{c("backDesign")}</Link><Link to="/admin/audit">{c("oldAudit")}</Link><button onClick={refresh}>{c("refresh")}</button></nav></header>
  {(overview.isError||operations.isError||actions.isError||detail.isError)?<p role="alert">{c("readFailure")}</p>:null}
  <section aria-label={c("controls")}>
   <h2>{c("controls")}</h2>
   <p>{overview.isPending?c("loading"):overview.isError?c("stateUnknown"):paused?c("paused"):c("allowed")}</p>
   <button disabled={!overview.data||!!saved||pending} onClick={()=>choose(paused?"resume":"pause")}>{paused?c("label_resume"):c("label_pause")}</button>
   <p>{c("pauseHelp")}</p>
   <p>{c("capacity")}</p>
   {overview.data?<p>{c("counts",{requests:overview.data.recovery_requests,completed:overview.data.recovery_completed,duplicates:overview.data.duplicate_requests})}</p>:null}
  </section>
  <UsageCoverage value={overview.data?.runtime_usage}/>
  <ContextCoverage value={overview.data?.context_recovery}/>
  <HealthSignals value={overview.data?.health} onOperation={setSelected}/>
  {saved?<section aria-label={c("unconfirmed")}><h2>{c("unconfirmed")}</h2><p>{label(saved.action)} · {saved.target_id}</p><p>{saved.reason}</p>
   <p>{c("requestIdentity",{id:saved.client_request_id})}</p><button disabled={pending} onClick={()=>void execute(saved)}>{c("retryOriginal")}</button></section>:null}
  {form&&!saved?<form aria-label={c("form")} onSubmit={e=>{e.preventDefault();const parsed=commandSchema.safeParse({...form,
   client_request_id:crypto.randomUUID(),reason:reason.trim(),...(form.action==="refund"?{units}:{})});
   if(parsed.success)void execute(parsed.data);else setError("recovery_form_invalid");}}>
   <h2>{label(form.action)}</h2><p>{c("target",{id:form.target_id})}</p>
   {form.action==="reconcile"?<p>{c("reconcileHelp")}</p>:form.action==="retry_publish"?<p>{c("publishHelp")}</p>
    :form.action==="quarantine"?<p>{c("quarantineHelp")}</p>:null}
   <label>{c("reason")}<textarea autoFocus required minLength={3} maxLength={500} value={reason} onChange={e=>setReason(e.target.value)}/></label>
   {form.action==="refund"?<label>{c("refundUnits")}<input type="number" min={1} max={op?op.settled_units-op.refunded_units:100000} required value={units} onChange={e=>setUnits(Number(e.target.value))}/></label>:null}
   <button type="submit" disabled={pending||reason.trim().length<3}>{confirm(form.action)}</button><button type="button" onClick={()=>setForm(null)}>{c("cancel")}</button>
  </form>:null}
  {Boolean(error)?<p role="alert">{message(error)}</p>:null}{result?<p role="status">{label(result.status)} · {result.id}</p>:null}
  <section aria-label={c("operationList")}><h2>{c("operations")}</h2><label htmlFor="admin-operation-state">{c("state")}</label><select id="admin-operation-state" value={state} onChange={e=>{setState(e.target.value);setOffset(0);setSelected(null);}}>
   {["","reconciling","publishing","queued","reviewing","running","cancelling","succeeded","partial_success","failed","cancelled","awaiting_input"].map(s=><option key={s} value={s}>{s?label(s):c("all")}</option>)}
  </select>
  <div className="v2-recovery-table"><table><thead><tr><th>{c("operationId")}</th><th>{c("state")}</th><th>{c("settled")}</th><th>{c("created")}</th></tr></thead>
   <tbody>{operations.data?.items.map(row=><tr key={row.id}><td><button onClick={()=>setSelected(row.id)}>{row.id}</button></td><td>{label(row.state)}</td><td>{row.settled_units}</td><td>{date(row.created_at)}</td></tr>)}</tbody></table></div>
  {operations.data?.items.length===0?<p>{c("noOperations")}</p>:null}
  <button disabled={offset===0} onClick={()=>setOffset(n=>Math.max(0,n-30))}>{c("previous")}</button><button disabled={!operations.data?.has_more} onClick={()=>setOffset(n=>n+30)}>{c("next")}</button>
  </section>
  {op?<section aria-label={c("detail")}><h2>{c("detail")}</h2><p>{op.id} · {label(op.state)}</p><p>{c("owner",{id:op.owner_id})}</p>
   <p>{c("balances",{settled:op.settled_units,refunded:op.refunded_units,reserved:op.wallet?.reserved??c("unknown")})}</p>
   <p>{c("upstream",{state:op.upstream_status??c("unknown")})}</p>
   {op.automatic_recovery?<p>{c("automatic",{state:automatic(op.automatic_recovery.state),attempts:op.automatic_recovery.attempts,max:op.automatic_recovery.max_attempts})}{op.automatic_recovery.next_check_at?" · "+c("earliest",{date:date(op.automatic_recovery.next_check_at)}):""}</p>:null}
   <p>{op.stages.map((s,i)=><span key={i}>{c("duration",{state:label(s.state),seconds:s.seconds.toFixed(1)})} </span>)}</p>
   <UsageDetails value={op.runtime_usage}/>
   <div className="v2-recovery-actions">
    <button disabled={!!saved||pending||op.busy||quarantined||!["reconciling","cancelling"].includes(op.state)} onClick={()=>choose("reconcile",op.id)}>{c("label_reconcile")}</button>
    <button disabled={!!saved||pending||op.busy||op.state!=="publishing"||!op.has_staged_output||!op.upstream_status} onClick={()=>choose("retry_publish",op.id)}>{c("label_retry_publish")}</button>
    <button disabled={!!saved||pending||op.settled_units<=op.refunded_units} onClick={()=>choose("refund",op.id)}>{c("label_refund")}</button>
    <button disabled={!!saved||pending} onClick={()=>choose(quarantined?"unquarantine":"quarantine",op.owner_id)}>{quarantined?c("label_unquarantine"):c("label_quarantine")}</button>
   </div>
  </section>:null}
  <section aria-label={c("audit")}><h2>{c("audit")}</h2>
   <ol className="v2-recovery-audit">{actions.data?.items.map(a=><li key={a.id}><strong>{label(a.action)} · {label(a.status)}</strong><p>{a.target_id}</p>
    <p>{c("actor",{id:a.actor_id,date:date(a.created_at)})}</p><p>{a.reason}</p>
    <p>{c("auditRequest",{id:a.client_request_id,count:a.replay_count})}{a.error_code?" · "+message(new RecoveryError(a.error_code,400)):""}</p>
   </li>)}</ol>
   <button disabled={auditOffset===0} onClick={()=>setAuditOffset(n=>Math.max(0,n-30))}>{c("newer")}</button><button disabled={!actions.data?.has_more} onClick={()=>setAuditOffset(n=>n+30)}>{c("older")}</button>
  </section>
 </main>;
}
