import {useTranslation} from "react-i18next";
import type copy from "./page-copy-zh-CN";
export type DesignCopyKey=keyof typeof copy;
export function useDesignCopy(){
 const {t,i18n}=useTranslation();
 return {c:(key:DesignCopyKey,values?:Record<string,string|number>)=>t("v2Design."+key,values),
  language:i18n.resolvedLanguage??i18n.language};
}
