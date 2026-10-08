import {useMessageCopy} from "./useMessageCopy";
import {useSyncExternalStore} from "react";
import {IconArrowDown} from "@tabler/icons-react";
import type {unreadSignal} from "./conversationPosition";
export function NewContentButton({signal,onClick}:{signal:ReturnType<typeof unreadSignal>;onClick:()=>void}){
 const {c}=useMessageCopy();
 const visible=useSyncExternalStore(signal.subscribe,signal.get,signal.get);
 return <div className="v2-new-content" aria-live="polite">{visible?
  <button className="v2-icon v2-round" aria-label={c("newContent")} title={c("newContent")} onClick={onClick}><IconArrowDown size={18} stroke={1.75}/><span className="v2-unread-dot" aria-hidden="true"/></button>:null}</div>;
}
