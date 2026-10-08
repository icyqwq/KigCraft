import {useTranslation} from "react-i18next";
import type copy from "./viewset-copy-zh-CN";
import {errorText} from "./api";
export type ViewsetCopyKey=keyof typeof copy;
/** Copy for the four-view dialog. */
export function useViewsetCopy(){
 const {t,i18n}=useTranslation(),language=i18n.resolvedLanguage??i18n.language;
 const c=(key:ViewsetCopyKey,values?:Record<string,string|number>)=>t("v2Viewsets."+key,values);
 return {c,language,message:(error:unknown)=>errorText(error,language)};
}
