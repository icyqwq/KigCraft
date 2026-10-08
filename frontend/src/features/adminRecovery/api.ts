import {z} from "zod";
export const commandSchema=z.object({
 client_request_id:z.string().min(8).max(100),action:z.enum(["pause","resume","quarantine","unquarantine","refund","reconcile","retry_publish"]),
 target_id:z.string().min(1).max(36),reason:z.string().trim().min(3).max(500),units:z.number().int().positive().max(100000).nullable().optional(),
}).strict();
export type Command=z.infer<typeof commandSchema>;
const action=z.object({
 id:z.string(),actor_id:z.string(),client_request_id:z.string(),action:z.union([commandSchema.shape.action,z.enum(["expire_uploads","purge_uploads","restore_uploads","retire_runtime","purge_runtime","retire_runtime_orphans","purge_runtime_orphans","configure_rollout","retire_backups","purge_backups","expire_trash_assets","purge_trash_assets","scrub_deleted_drafts","scrub_design_content","scrub_account_content","retire_account_files","purge_account_files","request_account_closure","cancel_account_closure","close_account","retire_native_history","purge_native_history","migrate_minio_assets","retire_minio_orphans","purge_minio_orphans"])]),target_id:z.string(),reason:z.string(),
 units:z.number().nullable(),status:z.enum(["pending","running","completed","failed","unknown","unresolved"]),
 result:z.record(z.unknown()),error_code:z.string().nullable(),replay_count:z.number(),created_at:z.string(),
 started_at:z.string().nullable(),completed_at:z.string().nullable(),
});
const operation=z.object({
 id:z.string(),owner_id:z.string(),design_id:z.string(),kind:z.string(),state:z.string(),
 reserved_units:z.number(),settled_units:z.number(),reservation_closed:z.boolean(),upstream_status:z.string().nullable(),
 dispatch_intent_at:z.string().nullable(),created_at:z.string(),updated_at:z.string(),
});
const usageCount=z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
export const usageBreakdown=z.object({
 inputTokens:usageCount,cachedInputTokens:usageCount,outputTokens:usageCount,
 reasoningOutputTokens:usageCount,totalTokens:usageCount,cacheWriteInputTokens:usageCount.nullable(),
});
const tokenSnapshot=z.object({last:usageBreakdown,total:usageBreakdown,modelContextWindow:usageCount.nullable()});
export const runtimeUsage=z.object({
 status:z.enum(["observed","unknown"]),turn_tokens:z.null(),provider_model_requests:z.null(),amount:z.null(),
 attempts:z.array(z.object({
  phase:z.enum(["review","generation"]),requested_model:z.string(),codex_version:z.string(),
  state:z.enum(["dispatch_intent","started","completed","interrupted","failed"]),turn_acknowledged:z.boolean(),
  token_status:z.enum(["unknown","observed","invalid"]),token_scope:z.literal("upstream_thread_snapshot"),
  token_snapshot:tokenSnapshot.nullable(),observed_image_items:usageCount,image_tool_calls:z.null(),
  image_anomalies:z.object({replayed_items:usageCount,conflicting_items:usageCount,extra_completed_items:usageCount}).nullable().optional(),
  image_coverage:z.enum(["partial","through_terminal"]),created_at:z.string(),updated_at:z.string(),
 }).superRefine((value,ctx)=>{
  if((value.token_status==="observed")!==(value.token_snapshot!==null))
   ctx.addIssue({code:z.ZodIssueCode.custom,message:"Inconsistent usage snapshot"});
 })),
});
export const runtimeUsageTotals=z.object({
 dispatch_intents:usageCount,acknowledged_attempts:usageCount,reported_snapshots:usageCount,
 missing_snapshots:usageCount,invalid_snapshots:usageCount,operations_without_observations:usageCount,
 turn_tokens:z.null(),provider_model_requests:z.null(),amount:z.null(),
});
const contextMethod=z.object({
 attempts:usageCount,confirmed:usageCount,unconfirmed:usageCount,
 confirmation_rate:z.number().min(0).max(1).nullable(),
}).superRefine((value,ctx)=>{
 const expected=value.attempts?value.confirmed/value.attempts:null;
 if(value.attempts!==value.confirmed+value.unconfirmed||value.confirmation_rate!==expected)
  ctx.addIssue({code:z.ZodIssueCode.custom,message:"Inconsistent context counts"});
});
export const contextRecovery=z.object({
 scope:z.literal("main_session_open"),since:z.string().nullable(),
 methods:z.object({start:contextMethod,resume:contextMethod,fork:contextMethod}),
 dispatched_operations_without_observations:usageCount,
});
export type ContextRecovery=z.infer<typeof contextRecovery>;
export type RuntimeUsage=z.infer<typeof runtimeUsage>;
export type RuntimeUsageTotals=z.infer<typeof runtimeUsageTotals>;

const detail=operation.extend({
 runtime_usage:runtimeUsage.optional(),
 automatic_recovery:z.object({state:z.string(),attempts:z.number(),max_attempts:z.number(),next_check_at:z.string().nullable(),last_result:z.string().nullable()}).optional(),
 stages:z.array(z.object({state:z.string(),seconds:z.number()})),refunded_units:z.number(),
 wallet:z.object({balance:z.number(),reserved:z.number()}).nullable(),has_staged_output:z.boolean(),busy:z.boolean(),
 upstream_cost:z.object({status:z.string(),amount:z.number().nullable()}),
});
export const healthReport=z.object({
 checked_at:z.string(),stalled_seconds:usageCount,recent_seconds:usageCount,
 signals:z.array(z.object({
  code:z.enum(["expired_leases","publishing_backlog","unresolved_operations","wallet_mismatch",
   "review_failures","storage_capacity","runtime_identity","duplicate_outputs","access_denials"]),
  status:z.enum(["active","clear","unknown"]),severity:z.enum(["warning","critical"]),
  count:usageCount.nullable(),threshold:usageCount,operation_ids:z.array(z.string()).max(5),
  free_bytes:usageCount.nullable().optional(),minimum_free_bytes:usageCount.optional(),
  scope:z.literal("minio").optional(),
  capacity:z.object({
   cluster:z.object({status:z.enum(["active","clear","unknown"]),free_bytes:usageCount.nullable(),total_bytes:usageCount.optional()}),
   bucket:z.object({status:z.enum(["active","clear","unknown"]),free_bytes:usageCount.nullable(),
    quota_bytes:usageCount.optional(),used_bytes:usageCount.optional(),age_seconds:usageCount.optional(),unlimited:z.boolean().optional()}),
  }).optional(),
  categories:z.object({cross:usageCount,miss:usageCount,unauthenticated:usageCount,forbidden:usageCount,rejected:usageCount,stream_closed:usageCount}).optional(),
  coverage:z.object({
   scope:z.enum(["authenticated_v2_path_resources","authenticated_v2_resource_references","private_resource_rejections"]),status:z.enum(["complete","incomplete"]),
   active_processes:usageCount,expected_processes:usageCount,stale_processes:usageCount,
   gap_recorded:z.boolean(),inventory_complete:z.boolean(),
  }).optional(),
 })),
});
export type HealthReport=z.infer<typeof healthReport>;
const overview=z.object({
 health:healthReport.optional(),
 context_recovery:contextRecovery.optional(),
 runtime_usage:runtimeUsageTotals.optional(),
 controls:z.record(z.boolean()),operation_counts:z.record(z.number()),recovery_requests:z.number(),recovery_completed:z.number(),
 duplicate_requests:z.number(),upstream_cost:z.object({status:z.string(),amount:z.number().nullable()}),
 platform_capacity:z.object({status:z.string()}),
});
export class RecoveryError extends Error {constructor(public code:string,public status:number){super(code);}}
async function call<S extends z.ZodTypeAny>(path:string,schema:S,body?:Command):Promise<z.output<S>>{
 const r=await fetch("/api/admin/v2"+path,{method:body?"POST":"GET",credentials:"same-origin",cache:"no-store",
  headers:{"Content-Type":"application/json"},...(body?{body:JSON.stringify(body)}:{})});
 if(!r.ok){const data=await r.json().catch(()=>({}));throw new RecoveryError(data.code??"request_failed",r.status);}
 return schema.parse(await r.json());
}
export const adminRecovery={
 overview:()=>call("/overview",overview),
 operations:(state:string,offset:number)=>call("/operations?"+new URLSearchParams({...(state?{state}:{}),offset:String(offset)}),z.object({items:z.array(operation),has_more:z.boolean()})),
 detail:(id:string)=>call("/operations/"+encodeURIComponent(id),detail),
 actions:(offset:number)=>call("/actions?offset="+offset,z.object({items:z.array(action),has_more:z.boolean()})),
 submit:async(command:Command)=>{
  const result=await call("/actions",action,command);
  if(result.client_request_id!==command.client_request_id||result.action!==command.action||result.target_id!==command.target_id||
   result.reason!==command.reason||result.units!==(command.units??null))throw new Error("Receipt mismatch");
  return result;
 },
};
