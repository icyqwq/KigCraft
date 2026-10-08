import {useTranslation} from "react-i18next";
import type copy from "./copy-zh-CN";
export type EditorCopyKey = keyof typeof copy;
/** Translate only UI resource keys. Documents, filenames and request payloads stay untouched. */
export function useEditorCopy() {
  const {t,i18n}=useTranslation();
  const c=(key:EditorCopyKey,values?:Record<string,string|number>)=>t("v2Editor."+key,values);
  return {c,language:i18n.resolvedLanguage??i18n.language};
}
