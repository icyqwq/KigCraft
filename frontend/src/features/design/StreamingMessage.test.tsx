import {act,cleanup,render,screen,waitFor} from "@testing-library/react";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {afterEach,expect,it,vi} from "vitest";
import {StreamingMessage,messageStreamKey} from "./StreamingMessage";
import {useDesignEvents} from "./useDesignEvents";
import type {Snapshot} from "./api";

class Source extends EventTarget {
 static instances:Source[]=[];
 onopen:(()=>void)|null=null;onerror:(()=>void)|null=null;closed=false;
 constructor(public url:string){super();Source.instances.push(this);}
 close(){this.closed=true;}
 send(sequence:number,text:string,type="message.delta"){
  this.dispatchEvent(new MessageEvent(type,{lastEventId:String(sequence),
   data:JSON.stringify({message_id:"message",branch_id:"branch",operation_id:"op",text})}));
 }
}
const message:Snapshot["messages"][number]={id:"message",branch_id:"branch",role:"assistant",status:"streaming",
 created_at:"2026-01-01T00:00:00Z",operation_id:"op",content_parts:[{type:"text",text:"Saved prefix"}]};
afterEach(()=>{cleanup();vi.unstubAllGlobals();Source.instances=[];});

it("updates only the message, with cumulative replacements and replay deduplication",async()=>{
 vi.stubGlobal("EventSource",Source);
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}}),refresh=vi.spyOn(client,"invalidateQueries");
 const progress=vi.fn();let parentRenders=0;
 function Parent(){parentRenders++;useDesignEvents("owner","design",10);
  return <StreamingMessage ownerId="owner" designId="design" message={message} snapshotSequence={10} onProgress={progress}/>;}
 render(<QueryClientProvider client={client}><Parent/></QueryClientProvider>);
 const count=parentRenders,source=Source.instances[0];
 act(()=>{source.send(11,"Hello");source.send(12,"Hello coral");source.send(12,"Duplicate must not append");});
 await screen.findByText("Hello coral");
 expect(parentRenders).toBe(count);expect(refresh).not.toHaveBeenCalled();expect(progress).toHaveBeenCalled();
 act(()=>source.send(11,"Old replay"));expect(screen.getByText("Hello coral")).toBeVisible();
 client.clear();
});

it("final and newer persisted snapshots override an older stream cache",async()=>{
 const client=new QueryClient();
 client.setQueryData(messageStreamKey("owner","design","message"),{sequence:12,branch_id:"branch",operation_id:"op",text:"Old stream"});
 const element=(value=message,sequence=13)=><QueryClientProvider client={client}>
  <StreamingMessage ownerId="owner" designId="design" message={value} snapshotSequence={sequence} onProgress={()=>{}}/></QueryClientProvider>;
 const view=render(element());expect(screen.getByText("Saved prefix")).toBeVisible();
 view.rerender(element({...message,status:"completed",content_parts:[{type:"text",text:"Final reply"}]},10));
 expect(screen.getByText("Final reply")).toBeVisible();expect(screen.queryByText("Old stream")).toBeNull();
 view.rerender(element({...message,status:"interrupted"}));expect(screen.getByText("回复已中断")).toBeVisible();
 client.clear();
});

it("keeps accounts, designs and operation identities separate",()=>{
 const client=new QueryClient();
 client.setQueryData(messageStreamKey("other","design","message"),{sequence:12,branch_id:"branch",operation_id:"op",text:"Other account"});
 client.setQueryData(messageStreamKey("owner","design","message"),{sequence:12,branch_id:"branch",operation_id:"other",text:"Wrong operation"});
 render(<QueryClientProvider client={client}><StreamingMessage ownerId="owner" designId="design" message={message}
  snapshotSequence={10} onProgress={()=>{}}/></QueryClientProvider>);
 expect(screen.getByText("Saved prefix")).toBeVisible();expect(screen.queryByText("Other account")).toBeNull();
 client.clear();
});

it("resyncs on a gap even if snapshot sequence is unchanged and ignores closed callbacks",async()=>{
 vi.stubGlobal("EventSource",Source);
 const client=new QueryClient(),refresh=vi.spyOn(client,"invalidateQueries").mockResolvedValue();
 function Connection(){return <span>{String(useDesignEvents("owner","design",10))}</span>;}
 const view=render(<QueryClientProvider client={client}><Connection/></QueryClientProvider>);
 const old=Source.instances[0];act(()=>old.send(12,"Gap"));
 await waitFor(()=>expect(Source.instances).toHaveLength(2));
 expect(refresh).toHaveBeenCalledTimes(1);expect(old.closed).toBe(true);
 act(()=>old.send(11,"Late closed connection"));
 expect(client.getQueryData(messageStreamKey("owner","design","message"))).toBeUndefined();
 view.unmount();client.clear();
});

it("rechecks authentication after stream failure and ignores closed errors",async()=>{
 vi.stubGlobal("EventSource",Source);
 const client=new QueryClient(),refresh=vi.spyOn(client,"invalidateQueries").mockResolvedValue();
 function Connection(){return <span>{String(useDesignEvents("owner","design",10))}</span>;}
 const view=render(<QueryClientProvider client={client}><Connection/></QueryClientProvider>);
 const source=Source.instances[0];
 act(()=>source.onerror?.());
 expect(refresh).toHaveBeenCalledWith({queryKey:["auth","me"],exact:true});
 await waitFor(()=>expect(refresh).toHaveBeenCalledWith({queryKey:["v2","owner"]}));
 view.unmount();refresh.mockClear();
 act(()=>source.onerror?.());
 expect(refresh).not.toHaveBeenCalled();client.clear();
});
