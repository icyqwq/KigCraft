import "./resources";
import {useTranslation} from "react-i18next";
import type copy from "./copy-zh-CN";
import {RecoveryError,type Command} from "./api";
type Key=keyof typeof copy;
const names:Record<string,Key>={
  "expire_trash_assets":"label_expire_trash_assets",
  "purge_trash_assets":"label_purge_trash_assets",
  "migrate_minio_assets":"label_migrate_minio_assets",
  "retire_minio_orphans": "label_retire_minio_orphans",
  "purge_minio_orphans": "label_purge_minio_orphans",
  "scrub_deleted_drafts":"label_scrub_deleted_drafts",
  "retire_native_history":"label_retire_native_history",
  "purge_native_history":"label_purge_native_history",
  "request_account_closure":"label_request_account_closure",
  "cancel_account_closure":"label_cancel_account_closure",
  "close_account":"label_close_account",
  "retire_account_files":"label_retire_account_files",
  "purge_account_files":"label_purge_account_files",
  "scrub_account_content":"label_scrub_account_content",
  "scrub_design_content":"label_scrub_design_content",
  "retire_backups":"label_retire_backups",
  "purge_backups":"label_purge_backups",
  "configure_rollout":"label_configure_rollout",
  "retire_runtime":"label_retire_runtime",
  "retire_runtime_orphans":"label_retire_runtime_orphans",
  "purge_runtime_orphans":"label_purge_runtime_orphans",
  "purge_runtime":"label_purge_runtime",
  "expire_uploads":"label_expire_uploads",
  "purge_uploads":"label_purge_uploads",
  "restore_uploads":"label_restore_uploads",
  "pause": "label_pause",
  "resume": "label_resume",
  "quarantine": "label_quarantine",
  "unquarantine": "label_unquarantine",
  "refund": "label_refund",
  "reconcile": "label_reconcile",
  "retry_publish": "label_retry_publish",
  "pending": "label_pending",
  "running": "label_running",
  "completed": "label_completed",
  "failed": "label_failed",
  "unknown": "label_unknown",
  "unresolved": "label_unresolved",
  "queued": "label_queued",
  "reviewing": "label_reviewing",
  "reconciling": "label_reconciling",
  "publishing": "label_publishing",
  "cancelling": "label_cancelling",
  "succeeded": "label_succeeded",
  "partial_success": "label_partial_success",
  "cancelled": "label_cancelled",
  "awaiting_input": "label_awaiting_input"
};
const automatic:Record<string,Key>={
  "idle": "auto_idle",
  "disabled": "auto_disabled",
  "quarantined": "auto_quarantined",
  "manual_required": "auto_manual_required",
  "running": "auto_running",
  "scheduled": "auto_scheduled"
};
const errors:Record<string,Key>={
  "idempotency_conflict": "error_idempotency_conflict",
  "refund_exceeds_settlement": "error_refund_exceeds_settlement",
  "operation_busy": "error_operation_busy",
  "recovery_already_requested": "error_recovery_already_requested",
  "recovery_unavailable": "error_recovery_unavailable",
  "runtime_quarantined": "error_runtime_quarantined",
  "runtime_not_configured": "error_runtime_not_configured",
  "quota_insufficient": "error_quota_insufficient"
};
const confirmations:Record<Command["action"],Key>={
  "pause": "confirm_pause",
  "resume": "confirm_resume",
  "quarantine": "confirm_quarantine",
  "unquarantine": "confirm_unquarantine",
  "refund": "confirm_refund",
  "reconcile": "confirm_reconcile",
  "retry_publish": "confirm_retry_publish"
};
export function useRecoveryCopy(){
 const {t,i18n}=useTranslation("v2Recovery"),language=i18n.resolvedLanguage??i18n.language;
 const c=(key:Key,values?:Record<string,string|number>)=>t(key,values);
 return {c,language,label:(value:string)=>Object.hasOwn(names,value)?c(names[value]):value,
  automatic:(value:string)=>Object.hasOwn(automatic,value)?c(automatic[value]):c("autoUnknown"),
  confirm:(value:Command["action"])=>c(confirmations[value]),
  message:(error:unknown)=>{
   if(error==="recovery_storage_failed")return c("storageFailure");
   if(error==="recovery_form_invalid")return c("formInvalid");
   return error instanceof RecoveryError&&Object.hasOwn(errors,error.code)?c(errors[error.code]):c("requestUnknown");
  },
  date:(value:string)=>new Date(value).toLocaleString(language),
 };
}
