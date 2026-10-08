import {useTranslation} from "react-i18next";
import type copy from "./editor-copy-zh-CN";
import {errorText} from "./api";
export type CloudEditorCopyKey=keyof typeof copy;
const localErrors:Record<string,CloudEditorCopyKey>={
  "cloud_editor_local_recovery_failed": "localRecoveryFailed",
  "cloud_editor_manual_recovery_failed": "manualRecoveryFailed",
  "cloud_editor_manual_unconfirmed": "manualUnconfirmed",
  "cloud_editor_save_unverified": "saveUnverified",
  "cloud_editor_request_busy": "requestBusy",
  "cloud_editor_request_record_failed": "requestRecordFailed",
  "cloud_editor_leave_record_failed": "leaveRecordFailed",
  "cloud_editor_save_record_failed": "saveRecordFailed",
  "cloud_editor_local_no_image": "noImage"
};
export function useCloudEditorCopy(){
 const {t,i18n}=useTranslation(),language=i18n.resolvedLanguage??i18n.language;
 const c=(key:CloudEditorCopyKey,values?:Record<string,string|number>)=>t("v2CloudEditor."+key,values);
 return {c,message:(error:unknown)=>typeof error==="string"&&Object.hasOwn(localErrors,error)?c(localErrors[error]):errorText(error,language)};
}
