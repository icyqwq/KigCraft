import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {messageStreamKey,type MessageStream} from "./StreamingMessage";

const eventNames = ["session.updated", "message.created", "message.delta", "message.completed",
  "operation.state_changed", "question.opened", "question.resolved", "image.ready",
  "revision.current_changed", "draft.conflicted", "quota.changed"];

export function useDesignEvents(userId: string | undefined, designId: string | undefined, sequence: number | undefined) {
  const client = useQueryClient();
  const [connected, setConnected] = useState(true);
  const [reconnect,setReconnect]=useState(0);
  useEffect(() => {
    if (!userId || !designId || sequence === undefined) return;
    let cursor = sequence;
    let disposed=false;
    let restart=false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const source = new EventSource("/api/v2/designs/" + encodeURIComponent(designId) + "/events?after=" + sequence);
    const refresh = (reopen=false) => {
      restart=restart||reopen;
      if (timer) return;
      timer = setTimeout(async () => {
        timer = undefined;
        await client.invalidateQueries({ queryKey: ["v2", userId] });
        if(!disposed&&restart)setReconnect(value=>value+1);
      }, 100);
    };
    const receive = (event: MessageEvent) => {
      if(disposed)return;
      const next = Number(event.lastEventId);
      if (!Number.isSafeInteger(next) || next <= cursor) return;
      if (next !== cursor + 1) { source.close(); refresh(true); return; }
      cursor = next;
      if(event.type==="message.delta"){
        try{
          const value:unknown=JSON.parse(event.data);
          if(!value||typeof value!=="object")throw new Error("Invalid stream");
          const part=value as Record<string,unknown>;
          if(typeof part.message_id!=="string"||typeof part.branch_id!=="string"||
             typeof part.operation_id!=="string"||typeof part.text!=="string")throw new Error("Invalid stream");
          client.setQueryData<MessageStream>(messageStreamKey(userId,designId,part.message_id),
            previous=>previous&&previous.sequence>=next?previous:
              {sequence:next,branch_id:part.branch_id as string,operation_id:part.operation_id as string,text:part.text as string});
        }catch{source.close();refresh(true);}
        return;
      }
      refresh();
    };
    for (const name of eventNames) source.addEventListener(name, receive as EventListener);
    source.addEventListener("resync_required", () => { if(!disposed){source.close();refresh(true);} });
    source.onopen = () => {if(!disposed)setConnected(true);};
    source.onerror = () => {
      if (disposed) return;
      setConnected(false);
      // SSE cannot expose an HTTP 401/403; recheck login before reconnecting indefinitely.
      void client.invalidateQueries({ queryKey: ["auth", "me"], exact: true });
      refresh();
    };
    return () => { disposed=true; source.close(); if (timer) clearTimeout(timer); };
  }, [client, userId, designId, sequence,reconnect]);
  return connected;
}
