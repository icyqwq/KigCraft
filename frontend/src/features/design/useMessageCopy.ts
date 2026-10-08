import {useTranslation} from "react-i18next";
import type copy from "./message-copy-zh-CN";
export type MessageCopyKey=keyof typeof copy;
export function useMessageCopy(){
 const {t,i18n}=useTranslation();
 return {c:(key:MessageCopyKey,values?:Record<string,string|number>)=>t("v2Messages."+key,values),
  language:i18n.resolvedLanguage??i18n.language};
}
