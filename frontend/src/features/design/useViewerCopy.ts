import {useTranslation} from "react-i18next";
import type copy from "./viewer-copy-zh-CN";
export type ViewerCopyKey=keyof typeof copy;
export function useViewerCopy(){
 const {t,i18n}=useTranslation();
 return {c:(key:ViewerCopyKey,values?:Record<string,string|number>)=>t("v2Viewer."+key,values),
  language:i18n.resolvedLanguage??i18n.language};
}
