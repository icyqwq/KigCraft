import {useTranslation} from "react-i18next";
import type copy from "./legacy-copy-zh-CN";
import {errorText} from "./api";
type Key=keyof typeof copy;
const views:Record<string,Key>={front:"view_front",composite:"view_composite",unknown:"view_unknown"};
const states:Record<string,Key>={completed:"state_completed",failed:"state_failed",cancelled:"state_cancelled",expired:"state_expired",unknown:"state_unknown"};
export function useLegacyCopy(){
 const {t,i18n}=useTranslation(),language=i18n.resolvedLanguage??i18n.language;
 const c=(key:Key,values?:Record<string,string|number>)=>t("v2Legacy."+key,values);
 return {c,view:(value:string)=>c(Object.hasOwn(views,value)?views[value]:"view_unknown"),
  state:(value:string)=>c(Object.hasOwn(states,value)?states[value]:"stateUnconfirmed"),
  message:(error:unknown)=>errorText(error,language)};
}
