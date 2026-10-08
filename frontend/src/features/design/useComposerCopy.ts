import {useTranslation} from "react-i18next";
import type copy from "./copy-zh-CN";
export type ComposerCopyKey=keyof typeof copy;
export function useComposerCopy(){
  const {t,i18n}=useTranslation();
  return {
    c:(key:ComposerCopyKey,values?:Record<string,string|number>)=>t("v2Composer."+key,values),
    language:i18n.resolvedLanguage??i18n.language,
  };
}
