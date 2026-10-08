import {useTranslation} from "react-i18next";
import type copy from "./copy-zh-CN";
export type ReferenceCopyKey=keyof typeof copy;
export function useReferenceCopy(){
  const {t}=useTranslation();
  return (key:ReferenceCopyKey,values?:Record<string,string|number>)=>t("v2References."+key,values);
}
